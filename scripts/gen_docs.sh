#!/usr/bin/env bash
# scripts/gen_docs.sh — Generate API docs locally from the running daemon.
#
# Starts the daemon briefly, fetches /openapi.json, saves to docs/api.json,
# then stops the daemon. Intended to be called via ./dev.sh docs.
# Never run this in GitHub Actions.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCS_DIR="$REPO_ROOT/docs"
OUTPUT="$DOCS_DIR/api.json"
ENV_FILE="$REPO_ROOT/.env"
VENV="$REPO_ROOT/.venv"

# Load env to get the HTTP port
set -a
[[ -f "$ENV_FILE" ]] && source "$ENV_FILE"   # shellcheck source=/dev/null
set +a

HTTP_PORT="${CASEDD_HTTP_PORT:-8080}"
BASE_URL="http://localhost:${HTTP_PORT}"

# Activate venv
# shellcheck source=/dev/null
source "$VENV/bin/activate"

echo "==> Checking if casedd is already running..."
PID_FILE="$REPO_ROOT/run/casedd.pid"
STARTED_HERE=false

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "    casedd is already running (PID $(cat "$PID_FILE")) — will not stop it after."
else
    echo "==> Starting casedd temporarily..."
    STARTED_HERE=true
    # Ensure no-FB so we don't need hardware
    export CASEDD_NO_FB=1
    export CASEDD_SOCKET_PATH="${CASEDD_SOCKET_PATH:-$REPO_ROOT/run/casedd.sock}"
    export CASEDD_PID_FILE="$PID_FILE"
    mkdir -p "$REPO_ROOT/run" "$REPO_ROOT/logs"
    nohup python -m casedd >> "$REPO_ROOT/logs/casedd.log" 2>&1 &
    launcher_pid=$!

    # Wait for daemon to write its own PID file and start accepting HTTP.
    for _ in {1..20}; do
        if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            break
        fi
        if ! kill -0 "$launcher_pid" 2>/dev/null; then
            break
        fi
        sleep 0.2
    done

    echo "    Waiting for HTTP server to be ready..."
    for _ in {1..15}; do
        if curl -sf "$BASE_URL/openapi.json" > /dev/null 2>&1; then
            break
        fi
        sleep 1
    done
fi

echo "==> Fetching OpenAPI schema from $BASE_URL/openapi.json"
mkdir -p "$DOCS_DIR"
curl -sf "$BASE_URL/openapi.json" | python3 -m json.tool --indent 2 > "$OUTPUT"
echo "==> Saved to $OUTPUT"

if [[ "$STARTED_HERE" == "true" ]]; then
    echo "==> Stopping temporarily started casedd..."
    kill "$(cat "$PID_FILE")" 2>/dev/null || true
    rm -f "$PID_FILE"
fi

echo "Done."
