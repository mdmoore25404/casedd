#!/usr/bin/env python3
"""Load-test helper: allocate random RAM and hold it for N seconds.

Fills memory with ``os.urandom`` data in configurable chunks to prevent the
OS page-deduplication (KSM) from collapsing identical pages and giving a
misleadingly low RSS reading.  All allocated buffers are released in the
``finally`` block even if the process is interrupted.

Usage:
    python loadtest/ram.py                  # allocate 512 MB for 30 s
    python loadtest/ram.py -s 30 -m 512     # plain MB number
    python loadtest/ram.py -s 60 -m 2G      # unit suffixes accepted
    python loadtest/ram.py -s 60 -m 2048MB  # long suffix form also ok
    ./loadtest/ram.py --help

Ctrl-C / SIGTERM releases all memory cleanly via the finally block.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time

# Default chunk size kept small enough to give visible allocation progress
# but large enough to amortise the os.urandom overhead.
_DEFAULT_CHUNK_MB = 64.0

# Accepted suffix → multiplier (relative to MB)
_UNIT_MULTIPLIERS: dict[str, float] = {
    "b":   1.0 / (1024 * 1024),
    "k":   1.0 / 1024,
    "kb":  1.0 / 1024,
    "m":   1.0,
    "mb":  1.0,
    "g":   1024.0,
    "gb":  1024.0,
    "t":   1024.0 * 1024,
    "tb":  1024.0 * 1024,
}

_SIZE_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([a-zA-Z]*)\s*$")


def _parse_size_mb(raw: str) -> float:
    """Parse a size string into a float number of megabytes.

    Accepts plain numbers (interpreted as MB) or a number followed by a unit
    suffix.  Suffixes are case-insensitive.  Supported: ``B``, ``K``/``KB``,
    ``M``/``MB``, ``G``/``GB``, ``T``/``TB``.

    Args:
        raw: Raw size string from the command line, e.g. ``"512"``,
             ``"2G"``, ``"1.5GB"``, ``"2048MB"``.

    Returns:
        Equivalent size in megabytes as a float.

    Raises:
        argparse.ArgumentTypeError: On unrecognised format or unit.
    """
    m = _SIZE_RE.match(raw)
    if not m:
        raise argparse.ArgumentTypeError(
            f"invalid size '{raw}' — expected a number with optional unit "
            "(e.g. 512, 512M, 2G, 1.5GB)"
        )
    number_str, suffix = m.group(1), m.group(2).lower()
    multiplier = _UNIT_MULTIPLIERS.get(suffix if suffix else "m")
    if multiplier is None:
        known = ", ".join(sorted({k.upper() for k in _UNIT_MULTIPLIERS}))
        raise argparse.ArgumentTypeError(
            f"unknown unit '{m.group(2)}' — supported: {known}"
        )
    return float(number_str) * multiplier



def main() -> None:
    """Parse args, allocate random memory, hold, then release."""
    parser = argparse.ArgumentParser(
        description="Allocate random RAM to stress the memory subsystem.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ./loadtest/ram.py -s 30 -m 512     # 512 MB for 30 s\n"
            "  ./loadtest/ram.py -s 60 -m 2G      # 2 GB for 60 s\n"
            "  ./loadtest/ram.py -s 60 -m 1.5GB   # 1.5 GB for 60 s\n"
            "  ./loadtest/ram.py -s 30 -m 2048MB  # same as 2G\n"
        ),
    )
    parser.add_argument(
        "-s", "--seconds",
        type=float,
        default=30.0,
        metavar="N",
        help="How long to hold the allocation in seconds (default: 30)",
    )
    parser.add_argument(
        "-m", "--mb",
        type=_parse_size_mb,
        default=512.0,
        metavar="SIZE",
        help="Amount of RAM to allocate; accepts plain MB number or unit suffix "
             "(e.g. 512, 512M, 2G, 1.5GB — default: 512M)",
    )
    parser.add_argument(
        "--chunk-mb",
        type=_parse_size_mb,
        default=_DEFAULT_CHUNK_MB,
        metavar="SIZE",
        help=f"Chunk size per allocation step; same unit suffixes accepted "
             f"(default: {_DEFAULT_CHUNK_MB:.0f}M)",
    )
    args = parser.parse_args()

    if args.seconds <= 0:
        sys.exit("error: --seconds must be positive")
    if args.mb <= 0:
        sys.exit("error: --mb must be positive")
    if args.chunk_mb <= 0:
        sys.exit("error: --chunk-mb must be positive")

    target_bytes = int(args.mb * 1024 * 1024)
    chunk_bytes = int(args.chunk_mb * 1024 * 1024)

    print(
        f"[ram] Allocating {args.mb:.0f} MB in {args.chunk_mb:.0f} MB chunks "
        f"for {args.seconds:.0f} s — Ctrl-C to abort.",
        flush=True,
    )

    # Declared before try so the finally block can always reach it.
    chunks: list[bytearray] = []
    exit_code = 0

    try:
        allocated = 0
        while allocated < target_bytes:
            remaining = target_bytes - allocated
            this_chunk = min(chunk_bytes, remaining)
            # os.urandom fills with cryptographically random bytes, ensuring
            # every page is touched and preventing OS zero-page deduplication.
            chunks.append(bytearray(os.urandom(this_chunk)))
            allocated += this_chunk
            pct = 100.0 * allocated / target_bytes
            print(
                f"[ram]  {allocated // (1024 * 1024):>6} / {int(args.mb)} MB  "
                f"({pct:.0f}%)",
                flush=True,
            )

        print(
            f"[ram] Holding {args.mb:.0f} MB for {args.seconds:.0f} s…",
            flush=True,
        )
        time.sleep(args.seconds)
        print("[ram] Hold complete.", flush=True)

    except KeyboardInterrupt:
        print("\n[ram] Interrupted — releasing memory…", flush=True)
        exit_code = 130

    finally:
        chunks.clear()  # drop all references → GC collects immediately
        print("[ram] Memory released.", flush=True)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
