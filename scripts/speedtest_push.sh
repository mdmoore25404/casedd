#!/usr/bin/env bash
# Run Ookla speedtest immediately and push result to CASEDD via REST /update.
#
# Usage:
#   ./scripts/speedtest_push.sh [--server-id ID]

set -euo pipefail

if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

SERVER_ID="${CASEDD_SPEEDTEST_SERVER_ID:-}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --server-id|-s)
            if [[ $# -lt 2 ]]; then
                echo "error: --server-id requires a value" >&2
                exit 1
            fi
            SERVER_ID="$2"
            shift 2
            ;;
        --help|-h)
            cat <<'EOF'
Usage: ./scripts/speedtest_push.sh [--server-id ID]

Options:
    -s, --server-id ID   Force Ookla server ID (overrides CASEDD_SPEEDTEST_SERVER_ID)
    -h, --help           Show this help message
EOF
            exit 0
            ;;
        *)
            echo "error: unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

HTTP_PORT="${CASEDD_HTTP_PORT:-8080}"
SPEEDTEST_BIN="${CASEDD_SPEEDTEST_BINARY:-speedtest}"
ADV_DOWN="${CASEDD_SPEEDTEST_ADVERTISED_DOWN_MBPS:-2000}"
ADV_UP="${CASEDD_SPEEDTEST_ADVERTISED_UP_MBPS:-200}"
REF_DOWN="${CASEDD_SPEEDTEST_REFERENCE_DOWN_MBPS:-}"
REF_UP="${CASEDD_SPEEDTEST_REFERENCE_UP_MBPS:-}"
MARGINAL_RATIO="${CASEDD_SPEEDTEST_MARGINAL_RATIO:-0.9}"
CRITICAL_RATIO="${CASEDD_SPEEDTEST_CRITICAL_RATIO:-0.7}"

SPEEDTEST_ARGS=("--accept-license" "--accept-gdpr" "--format=json")
if [[ -n "${SERVER_ID}" ]]; then
    SPEEDTEST_ARGS+=("--server-id=${SERVER_ID}")
fi

RAW_JSON="$(${SPEEDTEST_BIN} "${SPEEDTEST_ARGS[@]}")"

PAYLOAD="$(RAW_JSON="$RAW_JSON" ADV_DOWN="$ADV_DOWN" ADV_UP="$ADV_UP" REF_DOWN="$REF_DOWN" REF_UP="$REF_UP" MARGINAL_RATIO="$MARGINAL_RATIO" CRITICAL_RATIO="$CRITICAL_RATIO" python3 - <<'PY'
import json
import os
from datetime import datetime

raw = json.loads(os.environ["RAW_JSON"])
adv_down = float(os.environ["ADV_DOWN"])
adv_up = float(os.environ["ADV_UP"])
ref_down_env = os.environ.get("REF_DOWN", "").strip()
ref_up_env = os.environ.get("REF_UP", "").strip()
ref_down = float(ref_down_env) if ref_down_env else adv_down
ref_up = float(ref_up_env) if ref_up_env else adv_up
ref_down = min(ref_down, adv_down)
ref_up = min(ref_up, adv_up)
marginal = float(os.environ["MARGINAL_RATIO"])
critical = float(os.environ["CRITICAL_RATIO"])

mbit = 1_000_000.0
down_mbps = float(raw["download"]["bandwidth"]) * 8.0 / mbit
up_mbps = float(raw["upload"]["bandwidth"]) * 8.0 / mbit
ping = float(raw["ping"]["latency"])
jitter = float(raw["ping"]["jitter"])

def status(ratio: float) -> str:
    if ratio < critical:
        return "critical"
    if ratio < marginal:
        return "marginal"
    return "good"

ratio_down = down_mbps / adv_down
ratio_up = up_mbps / adv_up
ratio_down_ref = down_mbps / ref_down
ratio_up_ref = up_mbps / ref_up
summary = (
    f"Down {down_mbps:.1f} Mb/s ({status(ratio_down)}) | "
    f"Up {up_mbps:.1f} Mb/s ({status(ratio_up)}) | "
    f"Ping {ping:.1f} ms | Jitter {jitter:.1f} ms"
)
compact = (
    f"DL {down_mbps:.0f} | UL {up_mbps:.0f} Mb/s\n"
    f"{ping:.1f} ms / {jitter:.1f} ms"
)
simple = f"{down_mbps:.0f} / {up_mbps:.0f} Mb/s"

server = raw.get("server") if isinstance(raw.get("server"), dict) else {}

payload = {
    "update": {
        "speedtest.download_mbps": round(down_mbps, 2),
        "speedtest.upload_mbps": round(up_mbps, 2),
        "speedtest.ping_ms": round(ping, 2),
        "speedtest.jitter_ms": round(jitter, 2),
        "speedtest.download_pct_adv": round(ratio_down * 100.0, 2),
        "speedtest.upload_pct_adv": round(ratio_up * 100.0, 2),
        "speedtest.download_pct_ref": round(ratio_down_ref * 100.0, 2),
        "speedtest.upload_pct_ref": round(ratio_up_ref * 100.0, 2),
        "speedtest.download_status": status(ratio_down),
        "speedtest.upload_status": status(ratio_up),
        "speedtest.threshold_marginal_pct": round(marginal * 100.0, 1),
        "speedtest.threshold_critical_pct": round(critical * 100.0, 1),
        "speedtest.last_run": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "speedtest.summary": summary,
        "speedtest.simple_summary": simple,
        "speedtest.compact_summary": compact,
        "speedtest.server_id": str(server.get("id", "")),
        "speedtest.server_name": str(server.get("name", "")),
        "speedtest.server_location": str(server.get("location", "")),
        "speedtest.server_country": str(server.get("country", "")),
        "speedtest.server_host": str(server.get("host", "")),
    }
}
print(json.dumps(payload))
PY
)"

curl -sS -X POST "http://localhost:${HTTP_PORT}/update" \
  -H "Content-Type: application/json" \
  -d "${PAYLOAD}"

echo

if [[ -n "${SERVER_ID}" ]]; then
    echo "Speedtest pushed to CASEDD on http://localhost:${HTTP_PORT}/update (server-id=${SERVER_ID})"
else
    echo "Speedtest pushed to CASEDD on http://localhost:${HTTP_PORT}/update (server-id=auto)"
fi
