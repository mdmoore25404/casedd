#!/usr/bin/env bash
# Push a demo update into CASEDD via REST /update.
#
# Usage:
#   ./scripts/push_demo.sh [temp_f] [note]

set -euo pipefail

TEMP_F="${1:-72.0}"
NOTE="${2:-Hello from push_demo.sh}"
HTTP_PORT="${CASEDD_HTTP_PORT:-8080}"

curl -sS -X POST "http://localhost:${HTTP_PORT}/update" \
  -H "Content-Type: application/json" \
  -d "{\"update\":{\"outside_temp_f\":${TEMP_F},\"custom.note\":\"${NOTE}\"}}"

echo

echo "Pushed outside_temp_f=${TEMP_F}, custom.note='${NOTE}' to http://localhost:${HTTP_PORT}/update"
