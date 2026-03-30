#!/usr/bin/env python3
"""Load-test helper: simulate GPU VRAM pressure by pushing fake metrics to CASEDD.

Pushes ``nvidia.memory_percent`` (and companion nvidia.* metrics) via the
CASEDD REST API on a repeating interval.  Useful for exercising the
``nvidia.memory_percent`` trigger threshold independently of GPU compute load.

Usage:
    ./loadtest/vram.py                     # 92 % VRAM pressure for 60 s
    ./loadtest/vram.py -p 95 -s 120       # 95 % VRAM for 2 min
    ./loadtest/vram.py -p 80 --total 24576  # 80 % of a 24 GB card
    ./loadtest/vram.py --url http://192.168.1.10:8080 -p 90
    ./loadtest/vram.py --help

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
        print(f"[vram] HTTP {exc.code}: {exc.reason}", flush=True)
        return False
    except urllib.error.URLError as exc:
        print(f"[vram] Connection error: {exc.reason}", flush=True)
        return False


def main() -> None:
    """Parse args, push VRAM pressure metrics on a loop, then restore on exit."""
    parser = argparse.ArgumentParser(
        description="Simulate GPU VRAM pressure via CASEDD REST API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ./loadtest/vram.py -p 92 -s 60      # 92% VRAM for 60 s\n"
            "  ./loadtest/vram.py -p 95 -s 120     # 95% VRAM for 2 min\n"
            "  ./loadtest/vram.py -p 80 --total 24576  # 80% of a 24 GB card\n"
        ),
    )
    parser.add_argument(
        "-p", "--percent",
        type=float,
        default=92.0,
        metavar="N",
        help="Simulated VRAM utilisation %% (default: 92)",
    )
    parser.add_argument(
        "--total",
        type=float,
        default=12288.0,
        metavar="MB",
        help="Simulated total VRAM in MB (default: 12288 = 12 GB)",
    )
    parser.add_argument(
        "--gpu-percent",
        type=float,
        default=5.0,
        metavar="N",
        help="Background GPU compute %% (default: 5, kept low to isolate VRAM pressure)",
    )
    parser.add_argument(
        "--temp",
        type=float,
        default=55.0,
        metavar="N",
        help="Simulated GPU temperature in °C (default: 55)",
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

    vram_used_mb = args.total * args.percent / 100.0
    payload: dict[str, float] = {
        "nvidia.memory_percent": args.percent,
        "nvidia.memory_used_mb": round(vram_used_mb),
        "nvidia.memory_total_mb": args.total,
        "nvidia.percent": args.gpu_percent,
        "nvidia.temperature": args.temp,
        "nvidia.power_w": 40.0,  # low power — mostly idle compute
        "nvidia.gpu_count": 1.0,
    }

    label = (
        f"nvidia.memory_percent={args.percent:.0f}%  "
        f"({vram_used_mb:.0f}/{args.total:.0f} MB)  "
        f"nvidia.percent={args.gpu_percent:.0f}%"
    )
    print(
        f"[vram] Pushing [{label}] every {args.interval:.1f} s for {args.seconds:.0f} s"
        " — Ctrl-C to stop.",
        flush=True,
    )

    try:
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            ok = _post(args.url, payload)
            status = "OK" if ok else "FAIL"
            print(f"[vram] {status}  ({remaining:.0f} s remaining)", flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        # Restore to low VRAM values so the trigger clears quickly.
        idle: dict[str, float] = {
            "nvidia.memory_percent": 10.0,
            "nvidia.memory_used_mb": round(args.total * 0.1),
            "nvidia.memory_total_mb": args.total,
            "nvidia.percent": 0.0,
            "nvidia.temperature": 35.0,
            "nvidia.power_w": 15.0,
            "nvidia.gpu_count": 1.0,
        }
        _post(args.url, idle)
        print("[vram] Restored idle values.", flush=True)


if __name__ == "__main__":
    sys.exit(main() or 0)
