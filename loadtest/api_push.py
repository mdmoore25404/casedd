#!/usr/bin/env python3
"""Load-test helper: push metric values to the CASEDD REST API.

Injects one or more key=value pairs into the CASEDD data store via
``POST /api/update``, optionally repeating on an interval so you can hold
a value long enough to satisfy a trigger rule's ``duration`` requirement.

Usage:
    # Push a single snapshot (one-shot):
    ./loadtest/api_push.py cpu.percent=95

    # Push repeatedly every 2 s for 60 s (keeps trigger active through hold):
    ./loadtest/api_push.py cpu.percent=95 nvidia.percent=80 -s 60 -i 2

    # Target a different host/port:
    ./loadtest/api_push.py nvidia.temperature=90 --url http://192.168.1.10:8080

    # Restore values to normal once done (clears the trigger):
    ./loadtest/api_push.py cpu.percent=5 nvidia.percent=5 nvidia.temperature=35

Ctrl-C stops the repeat loop cleanly.
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
            return resp.status < 300
    except urllib.error.HTTPError as exc:
        print(f"[push] HTTP {exc.code}: {exc.reason}", flush=True)
        return False
    except urllib.error.URLError as exc:
        print(f"[push] Connection error: {exc.reason}", flush=True)
        return False


def _parse_kv(raw: str) -> tuple[str, float]:
    """Parse a ``key=value`` token where value is a float or int.

    Args:
        raw: Token of the form ``some.key=42`` or ``cpu.percent=90.5``.

    Returns:
        (key, numeric_value) tuple.

    Raises:
        ValueError: If the token is not in ``key=value`` form or value
            is not numeric.
    """
    if "=" not in raw:
        raise ValueError(f"expected key=value, got: {raw!r}")
    key, _, val_str = raw.partition("=")
    key = key.strip()
    if not key:
        raise ValueError(f"empty key in: {raw!r}")
    return key, float(val_str.strip())


def main() -> None:
    """Parse arguments and push values to CASEDD."""
    parser = argparse.ArgumentParser(
        description="Push metric values to the CASEDD REST API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ./loadtest/api_push.py cpu.percent=95\n"
            "  ./loadtest/api_push.py nvidia.percent=85 nvidia.temperature=88 -s 60 -i 2\n"
            "  ./loadtest/api_push.py cpu.percent=5   # restore / clear trigger\n"
        ),
    )
    parser.add_argument(
        "pairs",
        nargs="+",
        metavar="key=value",
        help="Data-store key/value pairs to push (e.g. cpu.percent=95)",
    )
    parser.add_argument(
        "-s", "--seconds",
        type=float,
        default=0.0,
        metavar="N",
        help="Total duration to keep pushing in seconds (0 = one-shot, default: 0)",
    )
    parser.add_argument(
        "-i", "--interval",
        type=float,
        default=2.0,
        metavar="N",
        help="Repeat interval in seconds when --seconds > 0 (default: 2)",
    )
    parser.add_argument(
        "--url",
        default=_DEFAULT_URL,
        metavar="URL",
        help=f"CASEDD HTTP base URL (default: {_DEFAULT_URL})",
    )
    args = parser.parse_args()

    if args.seconds < 0:
        sys.exit("error: --seconds must be >= 0")
    if args.interval <= 0:
        sys.exit("error: --interval must be positive")

    # Parse all key=value pairs up front so we fail fast on bad input.
    try:
        update: dict[str, float] = dict(_parse_kv(pair) for pair in args.pairs)
    except ValueError as exc:
        sys.exit(f"error: {exc}")

    keys_display = ", ".join(f"{k}={v}" for k, v in update.items())
    one_shot = args.seconds == 0.0

    if one_shot:
        print(f"[push] → {keys_display}", flush=True)
        ok = _post(args.url, update)
        sys.exit(0 if ok else 1)

    print(
        f"[push] Pushing [{keys_display}] every {args.interval:.1f} s "
        f"for {args.seconds:.0f} s — Ctrl-C to stop.",
        flush=True,
    )

    exit_code = 0
    end_ts = time.monotonic() + args.seconds

    try:
        while time.monotonic() < end_ts:
            remaining = max(0.0, end_ts - time.monotonic())
            ok = _post(args.url, update)
            status = "OK" if ok else "FAIL"
            print(
                f"[push] {status}  [{keys_display}]  "
                f"({remaining:.0f} s remaining)",
                flush=True,
            )
            sleep_for = min(args.interval, max(0.0, end_ts - time.monotonic()))
            if sleep_for > 0:
                time.sleep(sleep_for)

        print("[push] Done.", flush=True)

    except KeyboardInterrupt:
        print("\n[push] Interrupted.", flush=True)
        exit_code = 130

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
