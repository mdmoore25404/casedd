#!/usr/bin/env bash
# template_sanity.sh — force shipped templates via REST API and verify frame capture.
#
# Captures are written to a temporary directory under /tmp and deleted on exit
# by default to keep the workspace and git tree clean.

set -euo pipefail

BASE_URL="${CASEDD_SANITY_BASE_URL:-http://127.0.0.1:8080}"
PANEL_NAME="${CASEDD_SANITY_PANEL:-primary}"
KEEP_ARTIFACTS=0
INCLUDE_WEATHER_EXTERNAL=0

usage() {
  cat <<'EOF'
Usage: ./scripts/template_sanity.sh [options]

Options:
  --base-url URL               API base URL (default: http://127.0.0.1:8080)
  --panel NAME                 Panel name (default: primary)
  --include-weather-external   Also force weather_external (normally skipped)
  --keep                       Keep temporary artifacts under /tmp
  -h, --help                   Show help

Notes:
  - By default, weather_external is skipped because it depends on external data.
  - Artifacts are deleted automatically unless --keep is provided.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-url)
      BASE_URL="$2"
      shift
      ;;
    --panel)
      PANEL_NAME="$2"
      shift
      ;;
    --include-weather-external)
      INCLUDE_WEATHER_EXTERNAL=1
      ;;
    --keep)
      KEEP_ARTIFACTS=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
  shift
done

TMP_DIR="$(mktemp -d /tmp/casedd-template-sanity.XXXXXX)"

templates=(
  system_stats
  fans
  htop
  net_ports
  push_demo
  slideshow
  stats_over_slideshow
  sysinfo
  ups_status
  weather_nws
)

if [[ "$INCLUDE_WEATHER_EXTERNAL" -eq 1 ]]; then
  templates+=(weather_external)
fi

force_template() {
  local template_name="$1"
  curl -fsS -X POST "${BASE_URL}/api/template/override" \
    -H 'Content-Type: application/json' \
    -d "{\"panel\":\"${PANEL_NAME}\",\"template\":\"${template_name}\"}" \
    >/dev/null
}

clear_override() {
  curl -fsS -X POST "${BASE_URL}/api/template/override" \
    -H 'Content-Type: application/json' \
    -d "{\"panel\":\"${PANEL_NAME}\",\"template\":null}" \
    >/dev/null || true
}

cleanup_override() {
  clear_override
}

cleanup_all() {
  cleanup_override
  if [[ "$KEEP_ARTIFACTS" -eq 0 ]]; then
    rm -rf "$TMP_DIR"
  fi
}

trap cleanup_all EXIT

echo "Using temporary directory: $TMP_DIR"

for template_name in "${templates[@]}"; do
  echo "Forcing template: ${template_name}"
  force_template "$template_name"
  sleep 1.5
  out_path="${TMP_DIR}/${template_name}.png"
  curl -fsS "${BASE_URL}/image?panel=${PANEL_NAME}" -o "$out_path"
  file_info="$(file -b "$out_path")"
  size_info="$(stat -c '%s bytes' "$out_path")"
  echo "  Captured ${template_name}: ${file_info} (${size_info})"
done

echo "Clearing forced template override"
clear_override

echo "Sanity check completed successfully."
if [[ "$KEEP_ARTIFACTS" -eq 1 ]]; then
  echo "Artifacts kept in: $TMP_DIR"
else
  echo "Artifacts will be removed on exit."
fi