#!/usr/bin/env python3
"""Load-test helper: simulate GPU utilisation by pushing fake metrics to CASEDD.

Pushes ``nvidia.percent`` (and companion nvidia metrics) via the CASEDD REST
API on a repeating interval so the data store shows elevated GPU use.  This
is useful for testing the ``nvidia.percent`` trigger rule without needing an
actual GPU workload.

The script pushes a full set of nvidia.* keys so the ``nvidia_detail`` template
renders with believable values rather than zeros.

Usage:
    ./loadtest/gpu.py                      # 80 % GPU load for 60 s
    ./loadtest/gpu.py -p 95 -s 120        # 95 % for 2 min
    ./loadtest/gpu.py -p 60 --vram 70     # 60 % GPU, 70 % VRAM
    ./loadtest/gpu.py --url http://192.168.1.10:8080 -p 90
    ./loadtest/gpu.py --help

Ctrl-C stops the loop cleanly.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

_DEFAULT_URL = "http://localhost:8080"


def _post(base_url: str, update: dict[str, float]) -> bool:
    """POST the update payload to /api/update.

    Args:
        base_url: Base URL of the CASEDD HTTP server.
        update: Key/value pairs to push into the data store.

    Returns:
        True on success (2xx), False on connection or HTTP error.
    """
    url = f"{base_url.rstrip('/')}/api/update"
    body = json.dumps({"update": update}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status < 300  # type: ignore[no-any-return]
    except urllib.error.HTTPError as exc:
        print(f"[gpu] HTTP {exc.code}: {exc.reason}", flush=True)
        return False
    except urllib.error.URLError as exc:
        print(f"[gpu] Connection error: {exc.reason}", flush=True)
        return False


def main() -> None:
    """Parse args, push GPU metrics on a loop, then restore on exit."""
    parser = argparse.ArgumentParser(
        description="Simulate GPU utilisation via CASEDD REST API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ./loadtest/gpu.py -p 80 -s 60     # 80% GPU for 60 s\n"
            "  ./loadtest/gpu.py -p 95 -s 120    # 95% GPU for 2 min\n"
            "  ./loadtest/gpu.py -p 70 --vram 85 # 70% GPU + 85% VRAM\n"
        ),
    )
    parser.add_argument(
        "-p", "--percent",
        type=float,
        default=80.0,
        metavar="N",
        help="Simulated GPU utilisation %% (default: 80)",
    )
    parser.add_argument(
        "--vram",
        type=float,
        default=60.0,
        metavar="N",
        help="Simulated VRAM utilisation %% (default: 60)",
    )
    parser.add_argument(
        "--temp",
        type=float,
        default=72.0,
        metavar="N",
        help="Simulated GPU temperature in °C (default: 72)",
    )
    parser.add_argument(
        "--power",
        type=float,
        default=220.0,
        metavar="N",
        help="Simulated GPU power draw in W (default: 220)",
    )
    parser.add_argument(
        "--vram-total",
        type=float,
        default=12288.0,
        metavar="N",
        help="Simulated total VRAM in MB (default: 12288 = 12 GB)",
    )
    parser.add_argument(
        "-s", "--seconds",
        type=float,
        default=60.0,
        metavar="N",
        help="Duration to sustain the load in seconds (default: 60)",
    )
    parser.add_argument(
        "-i", "--interval",
        type=float,
        default=2.0,
        metavar="N",
        help="Push interval in seconds (default: 2)",
    )
    parser.add_argument(
        "--url",
        default=_DEFAULT_URL,
        metavar="URL",
        help=f"CASEDD base URL (default: {_DEFAULT_URL})",
    )
    args = parser.parse_args()

    vram_used_mb = args.vram_total * args.vram / 100.0
    payload: dict[str, float] = {
        "nvidia.percent": args.percent,
        "nvidia.temperature": args.temp,
        "nvidia.memory_percent": args.vram,
        "nvidia.memory_used_mb": round(vram_used_mb),
        "nvidia.memory_total_mb": args.vram_total,
        "nvidia.power_w": args.power,
        "nvidia.gpu_count": 1.0,
    }

    label = (
        f"nvidia.percent={args.percent:.0f}%  "
        f"nvidia.memory_percent={args.vram:.0f}%  "
        f"nvidia.temperature={args.temp:.0f}°C  "
        f"nvidia.power_w={args.power:.0f}W"
    )
    print(
        f"[gpu] Pushing [{label}] every {args.interval:.1f} s for {args.seconds:.0f} s"
        " — Ctrl-C to stop.",
        flush=True,
    )

    try:
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            ok = _post(args.url, payload)
            status = "OK" if ok else "FAIL"
            print(f"[gpu] {status}  ({remaining:.0f} s remaining)", flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        # Restore metrics to idle values so the trigger clears quickly.
        idle: dict[str, float] = {
            "nvidia.percent": 0.0,
            "nvidia.temperature": 35.0,
            "nvidia.memory_percent": 10.0,
            "nvidia.memory_used_mb": args.vram_total * 0.1,
            "nvidia.memory_total_mb": args.vram_total,
            "nvidia.power_w": 15.0,
            "nvidia.gpu_count": 1.0,
        }
        _post(args.url, idle)
        print("[gpu] Restored idle values.", flush=True)


if __name__ == "__main__":
    sys.exit(main() or 0)
