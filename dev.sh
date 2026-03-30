#!/usr/bin/env bash
# dev.sh — CASEDD development workflow script
#
# Usage: ./dev.sh <command>
#
# Commands:
#   start     Start daemon + advanced app dev server in the background
#   start-fb  Start dev daemon with real framebuffer output (/dev/fb*)
#   stop      Stop daemon + advanced app dev server cleanly
#   restart   Stop then start
#   status    Show daemon/web health + last 20 log lines
#   logs      Tail the log file (Ctrl-C to exit)
#   lint      Run ruff + mypy (must be zero errors before committing)
#   docs      Generate API docs to docs/api.json (local only)
#   pages     Serve GitHub Pages docs locally on http://localhost:4000
#   help      Show this message
#
# All paths are relative to the repo root (where this script lives).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$REPO_ROOT/.venv"
ENV_FILE="$REPO_ROOT/.env"
PID_FILE="$REPO_ROOT/run/casedd.pid"
LOG_FILE="$REPO_ROOT/logs/casedd.log"
DEV_PID_FILE="$REPO_ROOT/run/casedd-dev.pid"
DEV_LOG_FILE="$REPO_ROOT/logs/casedd-dev.log"
DEV_SOCKET_FILE="$REPO_ROOT/run/casedd-dev.sock"
DEV_FB_PREF_FILE="$REPO_ROOT/run/casedd-dev-use-fb.pref"
WEB_DIR="$REPO_ROOT/web"
WEB_PID_FILE="$REPO_ROOT/run/casedd-web.pid"
WEB_LOG_FILE="$REPO_ROOT/logs/casedd-web.log"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_activate_venv() {
    if [[ ! -f "$VENV/bin/activate" ]]; then
        echo "ERROR: venv not found at $VENV" >&2
        echo "Run: python3.12 -m venv .venv && pip install -r requirements.txt" >&2
        exit 1
    fi
    # shellcheck source=/dev/null
    source "$VENV/bin/activate"
}

_load_env() {
    if [[ -f "$ENV_FILE" ]]; then
        # Export each non-comment, non-blank line from .env
        set -a
        # shellcheck source=/dev/null
        source "$ENV_FILE"
        set +a
    fi
}

_ensure_dirs() {
    mkdir -p "$REPO_ROOT/run" "$REPO_ROOT/logs"
}

_prod_service_active() {
    if [[ "${CASEDD_DEV_FORCE_PROD_ISOLATION:-0}" == "1" ]]; then
        return 0
    fi

    if ! command -v systemctl >/dev/null 2>&1; then
        return 1
    fi

    systemctl is-active --quiet casedd.service
}

_prepare_socket_dir() {
    local socket_dir
    socket_dir="$(dirname "$CASEDD_SOCKET_PATH")"

    if [[ -w "$socket_dir" ]]; then
        return 0
    fi

    if mkdir -p "$socket_dir" 2>/dev/null; then
        return 0
    fi

    if ! command -v sudo >/dev/null 2>&1; then
        echo "ERROR: cannot create socket directory '$socket_dir' without sudo." >&2
        return 1
    fi

    echo "Preparing socket directory with sudo: $socket_dir"
    sudo mkdir -p "$socket_dir"
    sudo chown "$(id -u):$(id -g)" "$socket_dir"
    sudo chmod 775 "$socket_dir"

    if [[ ! -w "$socket_dir" ]]; then
        echo "ERROR: socket directory '$socket_dir' is still not writable." >&2
        return 1
    fi
}

_is_running() {
    [[ -f "$DEV_PID_FILE" ]] && kill -0 "$(cat "$DEV_PID_FILE")" 2>/dev/null
}

_is_web_running() {
    [[ -f "$WEB_PID_FILE" ]] && kill -0 "$(cat "$WEB_PID_FILE")" 2>/dev/null
}

_save_fb_pref() {
    echo "1" > "$DEV_FB_PREF_FILE"
}

_clear_fb_pref() {
    rm -f "$DEV_FB_PREF_FILE"
}

_should_use_fb() {
    [[ -f "$DEV_FB_PREF_FILE" ]] && [[ $(cat "$DEV_FB_PREF_FILE") == "1" ]]
}

_cleanup_web_processes() {
    local app_port
    app_port="${CASEDD_APP_PORT:-5173}"

    # Kill any stale listeners on the configured app port.
    local pids
    pids="$(ss -ltnp 2>/dev/null | grep -E ":${app_port}\\s" | grep -o 'pid=[0-9]\+' | cut -d= -f2 | sort -u || true)"
    if [[ -n "$pids" ]]; then
        while IFS= read -r stale_pid; do
            [[ -z "$stale_pid" ]] && continue
            kill "$stale_pid" 2>/dev/null || true
            sleep 0.1
            kill -9 "$stale_pid" 2>/dev/null || true
        done <<< "$pids"
    fi

    # Also clean up stale vite/npm processes from this repo's web app.
    pkill -f "$WEB_DIR/node_modules/.bin/vite" 2>/dev/null || true
    pkill -f "npm run dev -- --host 0.0.0.0 --port" 2>/dev/null || true
}

_start_web() {
    if _is_web_running; then
        echo "advanced app dev server is already running (PID $(cat "$WEB_PID_FILE"))"
        return 0
    fi

    if [[ ! -d "$WEB_DIR" ]]; then
        echo "WARNING: web/ directory not found; skipping advanced app dev server." >&2
        return 0
    fi

    if ! command -v npm >/dev/null 2>&1; then
        echo "WARNING: npm is not installed; skipping advanced app dev server startup." >&2
        return 0
    fi

    if [[ ! -d "$WEB_DIR/node_modules" ]]; then
        echo "Installing web dependencies..."
        if ! (cd "$WEB_DIR" && npm install); then
            echo "WARNING: npm install failed; advanced app dev server not started." >&2
            return 0
        fi
    fi

    local app_port
    app_port="${CASEDD_APP_PORT:-5173}"

    _cleanup_web_processes

    echo "Starting advanced app dev server (Vite)..."
    (
        cd "$WEB_DIR"
        # Run as its own process group so stop can terminate the full tree.
        nohup setsid npm run dev -- --host 0.0.0.0 --port "$app_port" \
            >> "$WEB_LOG_FILE" 2>&1 &
        echo $! > "$WEB_PID_FILE"
    )

    local waited=0
    while (( waited < 20 )); do
        if _is_web_running; then
            break
        fi
        sleep 0.2
        (( waited++ )) || true
    done

    if _is_web_running; then
        echo "advanced app dev server started (PID $(cat "$WEB_PID_FILE"))"
        echo "Advanced app: http://localhost:${app_port} (proxied by /app)"
    else
        echo "WARNING: advanced app dev server failed to start — check $WEB_LOG_FILE" >&2
        rm -f "$WEB_PID_FILE"
        return 0
    fi
}

_stop_web() {
    if ! _is_web_running; then
        rm -f "$WEB_PID_FILE"
        echo "advanced app dev server is not running"
        return 0
    fi

    local pid
    pid=$(cat "$WEB_PID_FILE")
    echo "Stopping advanced app dev server (PID $pid)..."
    kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true

    local waited=0
    while kill -0 "$pid" 2>/dev/null && (( waited < 10 )); do
        sleep 1
        (( waited++ )) || true
    done

    if kill -0 "$pid" 2>/dev/null; then
        echo "WARNING: app dev server did not stop in 10s — sending SIGKILL" >&2
        kill -9 -- "-$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null || true
    fi

    _cleanup_web_processes

    rm -f "$WEB_PID_FILE"
    echo "advanced app dev server stopped"
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

cmd_start() {
    if _is_running; then
        echo "casedd is already running (PID $(cat "$DEV_PID_FILE"))"
        return 0
    fi

    _activate_venv
    _load_env
    _ensure_dirs

    # Development profile:
    # - force verbose logging for active debugging
    # - disable framebuffer output by default so UI iteration uses the web viewer
    # Production/systemd runs should not use this script.
    export CASEDD_LOG_LEVEL="${CASEDD_DEV_LOG_LEVEL:-DEBUG}"
    export CASEDD_DEBUG_FRAME_LOGS="${CASEDD_DEV_DEBUG_FRAME_LOGS:-1}"

    # Check if user previously ran with -fb; honor it before exporting NO_FB.
    if _should_use_fb && ! _prod_service_active; then
        export CASEDD_DEV_NO_FB=0
    fi

    export CASEDD_NO_FB="${CASEDD_DEV_NO_FB:-1}"

    export CASEDD_PID_FILE="${CASEDD_DEV_PID_FILE:-$DEV_PID_FILE}"
    export CASEDD_LOG_DIR="${CASEDD_DEV_LOG_DIR:-$REPO_ROOT/logs}"

    # If a production systemd instance is alive, isolate dev mode so it does
    # not fight for the framebuffer or bind to the same viewer ports.
    if _prod_service_active; then
        echo "Production casedd.service detected — isolating dev runtime"
        export CASEDD_NO_FB=1
        export CASEDD_HTTP_PORT="${CASEDD_DEV_HTTP_PORT:-18080}"
        export CASEDD_WS_PORT="${CASEDD_DEV_WS_PORT:-18765}"
        # Clear preference since production is blocking framebuffer access
        _clear_fb_pref
    fi

    # In local dev, default to a repo-local Unix socket to avoid requiring
    # write access to /run/casedd. Deployments can override via .env.
    export CASEDD_SOCKET_PATH="${CASEDD_DEV_SOCKET_PATH:-$DEV_SOCKET_FILE}"

    _prepare_socket_dir

    echo "Starting casedd..."
    # cd to REPO_ROOT so `python -m casedd` resolves the package regardless of
    # the caller's working directory.
    nohup sh -c "cd '$REPO_ROOT' && exec python -m casedd" >> "$DEV_LOG_FILE" 2>&1 &
    local launcher_pid=$!

    # The daemon writes CASEDD_PID_FILE itself; wait briefly for that file
    # to appear and for the process to become alive.
    local waited=0
    while (( waited < 20 )); do
        if _is_running; then
            break
        fi
        if ! kill -0 "$launcher_pid" 2>/dev/null; then
            break
        fi
        sleep 0.2
        (( waited++ )) || true
    done

    if _is_running; then
        echo "casedd started (PID $(cat "$DEV_PID_FILE"))"
        echo "Log: $DEV_LOG_FILE"
        echo "Web viewer: http://localhost:${CASEDD_HTTP_PORT:-8080}"
        _start_web
    else
        echo "ERROR: casedd failed to start — check $DEV_LOG_FILE" >&2
        rm -f "$DEV_PID_FILE"
        exit 1
    fi
}

cmd_start_fb() {
    _load_env

    if _prod_service_active; then
        echo "ERROR: casedd.service is active; refusing to use /dev/fb in dev mode." >&2
        echo "Stop production first: sudo systemctl stop casedd.service" >&2
        exit 1
    fi

    _ensure_dirs
    _save_fb_pref
    export CASEDD_DEV_NO_FB=0
    echo "Production service is not active; starting dev mode with framebuffer enabled"
    cmd_start
}

cmd_stop() {
    _load_env
    _stop_web

    if ! _is_running; then
        echo "casedd is not running"
        rm -f "$DEV_PID_FILE"
        return 0
    fi

    local pid
    pid=$(cat "$DEV_PID_FILE")
    echo "Stopping casedd (PID $pid)..."
    kill "$pid"

    # Wait up to 10 seconds for clean exit
    local waited=0
    while kill -0 "$pid" 2>/dev/null && (( waited < 10 )); do
        sleep 1
        (( waited++ )) || true
    done

    if kill -0 "$pid" 2>/dev/null; then
        echo "WARNING: daemon did not stop in 10s — sending SIGKILL" >&2
        kill -9 "$pid" 2>/dev/null || true
    fi

    rm -f "$DEV_PID_FILE"
    echo "casedd stopped"
}

cmd_restart() {
    cmd_stop
    sleep 1
    # Restart will respect the stored framebuffer preference (if safe)
    cmd_start
}

cmd_status() {
    if _is_running; then
        echo "casedd is RUNNING (PID $(cat "$DEV_PID_FILE"))"
    else
        echo "casedd is STOPPED"
    fi

    if _is_web_running; then
        echo "advanced app dev server is RUNNING (PID $(cat "$WEB_PID_FILE"))"
    else
        echo "advanced app dev server is STOPPED"
    fi

    echo ""
    if [[ -f "$DEV_LOG_FILE" ]]; then
        echo "--- Last 20 log lines ---"
        tail -n 20 "$DEV_LOG_FILE"
    else
        echo "(no log file yet)"
    fi

    echo ""
    if [[ -f "$WEB_LOG_FILE" ]]; then
        echo "--- Last 20 advanced app log lines ---"
        tail -n 20 "$WEB_LOG_FILE"
    else
        echo "(no advanced app log file yet)"
    fi
}

cmd_logs() {
    if [[ ! -f "$DEV_LOG_FILE" ]]; then
        echo "No log file found at $DEV_LOG_FILE"
        exit 1
    fi
    tail -f "$DEV_LOG_FILE"
}

cmd_lint() {
    _activate_venv
    echo "==> ruff check ."
    ruff check .
    echo "==> mypy --strict casedd/"
    mypy --strict casedd/
    echo "Lint passed."
}

cmd_docs() {
    bash "$REPO_ROOT/scripts/gen_docs.sh"
}

cmd_pages() {
    echo "==> Serving GitHub Pages docs on http://localhost:4000"
    echo "    Press Ctrl-C to stop."
    docker run --rm \
        --volume "$REPO_ROOT/docs:/srv/jekyll:Z" \
        --publish 4000:4000 \
        --workdir /srv/jekyll \
        ruby:3.3 \
        bash -c "bundle install --quiet && bundle exec jekyll serve \
            --port 4000 --host 0.0.0.0" 2>&1
}

cmd_help() {
    grep '^#' "$0" | sed 's/^# \?//'
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

case "${1:-help}" in
    start)   cmd_start ;;
    start-fb) cmd_start_fb ;;
    stop)    cmd_stop ;;
    restart) cmd_restart ;;
    status)  cmd_status ;;
    logs)    cmd_logs ;;
    lint)    cmd_lint ;;
    docs)    cmd_docs ;;
    pages)   cmd_pages ;;
    help|--help|-h) cmd_help ;;
    *)
        echo "Unknown command: $1" >&2
        echo "Run './dev.sh help' for usage." >&2
        exit 1
        ;;
esac
