#!/usr/bin/env python3
"""Load-test helper: allocate random RAM and hold it for N seconds.

Fills memory with ``os.urandom`` data in configurable chunks to prevent the
OS page-deduplication (KSM) from collapsing identical pages and giving a
misleadingly low RSS reading.  All allocated buffers are released in the
``finally`` block even if the process is interrupted.

Usage:
    python loadtest/ram.py                  # allocate 512 MB for 30 s
    python loadtest/ram.py -s 30 -m 512     # explicit defaults
    python loadtest/ram.py -s 60 -m 2048    # hold 2 GB for 60 s
    ./loadtest/ram.py --help

Ctrl-C / SIGTERM releases all memory cleanly via the finally block.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

# Default chunk size kept small enough to give visible allocation progress
# but large enough to amortise the os.urandom overhead.
_DEFAULT_CHUNK_MB = 64.0


def main() -> None:
    """Parse args, allocate random memory, hold, then release."""
    parser = argparse.ArgumentParser(
        description="Allocate random RAM to stress the memory subsystem.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ./loadtest/ram.py -s 30 -m 512    # 512 MB for 30 s\n"
            "  ./loadtest/ram.py -s 60 -m 2048   # 2 GB for 60 s\n"
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
        type=float,
        default=512.0,
        metavar="N",
        help="Megabytes to allocate (default: 512)",
    )
    parser.add_argument(
        "--chunk-mb",
        type=float,
        default=_DEFAULT_CHUNK_MB,
        metavar="N",
        help=f"Allocation chunk size in MB (default: {_DEFAULT_CHUNK_MB:.0f})",
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
