#!/usr/bin/env bash
# dev.sh — CASEDD development workflow script
#
# Usage: ./dev.sh <command>
#
# Commands:
#   start     Start the daemon in the background (venv + .env loaded)
#   stop      Stop the running daemon cleanly
#   restart   Stop then start
#   status    Show daemon health + last 20 log lines
#   logs      Tail the log file (Ctrl-C to exit)
#   lint      Run ruff + mypy (must be zero errors before committing)
#   docs      Generate API docs to docs/api.json (local only)
#   help      Show this message
#
# All paths are relative to the repo root (where this script lives).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$REPO_ROOT/.venv"
ENV_FILE="$REPO_ROOT/.env"
PID_FILE="$REPO_ROOT/run/casedd.pid"
LOG_FILE="$REPO_ROOT/logs/casedd.log"

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

_is_running() {
    [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

cmd_start() {
    if _is_running; then
        echo "casedd is already running (PID $(cat "$PID_FILE"))"
        return 0
    fi

    _activate_venv
    _load_env
    _ensure_dirs

    echo "Starting casedd..."
    nohup python -m casedd >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    sleep 1   # brief pause to let the process write its own startup log

    if _is_running; then
        echo "casedd started (PID $(cat "$PID_FILE"))"
        echo "Log: $LOG_FILE"
        echo "Web viewer: http://localhost:${CASEDD_HTTP_PORT:-8080}"
    else
        echo "ERROR: casedd failed to start — check $LOG_FILE" >&2
        rm -f "$PID_FILE"
        exit 1
    fi
}

cmd_stop() {
    if ! _is_running; then
        echo "casedd is not running"
        rm -f "$PID_FILE"
        return 0
    fi

    local pid
    pid=$(cat "$PID_FILE")
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

    rm -f "$PID_FILE"
    echo "casedd stopped"
}

cmd_restart() {
    cmd_stop
    sleep 1
    cmd_start
}

cmd_status() {
    if _is_running; then
        echo "casedd is RUNNING (PID $(cat "$PID_FILE"))"
    else
        echo "casedd is STOPPED"
    fi
    echo ""
    if [[ -f "$LOG_FILE" ]]; then
        echo "--- Last 20 log lines ---"
        tail -n 20 "$LOG_FILE"
    else
        echo "(no log file yet)"
    fi
}

cmd_logs() {
    if [[ ! -f "$LOG_FILE" ]]; then
        echo "No log file found at $LOG_FILE"
        exit 1
    fi
    tail -f "$LOG_FILE"
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

cmd_help() {
    grep '^#' "$0" | sed 's/^# \?//'
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

case "${1:-help}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    restart) cmd_restart ;;
    status)  cmd_status ;;
    logs)    cmd_logs ;;
    lint)    cmd_lint ;;
    docs)    cmd_docs ;;
    help|--help|-h) cmd_help ;;
    *)
        echo "Unknown command: $1" >&2
        echo "Run './dev.sh help' for usage." >&2
        exit 1
        ;;
esac
