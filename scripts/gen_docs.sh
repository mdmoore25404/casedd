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
GETTER_TYPES_OUTPUT="$DOCS_DIR/getter_types.json"
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

echo "==> Generating getter type list from daemon source mapping"
python3 - <<PY
from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import re

repo_root = Path("$REPO_ROOT")
daemon_src = (repo_root / "casedd" / "daemon.py").read_text(encoding="utf-8")
prefixes = sorted(set(re.findall(r'\("([a-z0-9_]+)\.",\s*"[A-Za-z0-9_]+"\)', daemon_src)))

# Expand aggregate namespaces into common app names users search for.
expanded: list[str] = []
for prefix in prefixes:
    if prefix == "servarr":
        expanded.extend(["radarr", "sonarr", "servarr"])
    else:
        expanded.append(prefix)

payload = {
    "generated_at": datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC"),
    "getter_types": sorted(set(expanded)),
}
(repo_root / "docs" / "getter_types.json").write_text(
    json.dumps(payload, indent=2) + "\n",
    encoding="utf-8",
)
PY
echo "==> Saved to $GETTER_TYPES_OUTPUT"

echo "==> Optimizing docs images (incremental: changed sources only)"
python3 "$REPO_ROOT/scripts/optimize_docs_images.py"

if [[ "$STARTED_HERE" == "true" ]]; then
    echo "==> Stopping temporarily started casedd..."
    kill "$(cat "$PID_FILE")" 2>/dev/null || true
    rm -f "$PID_FILE"
fi

echo "Done."
