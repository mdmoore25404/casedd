#!/usr/bin/env python3
"""Load-test helper: stress CPU cores to a target utilisation percentage.

Uses a duty-cycle loop — each worker calculates Leibniz-series pi digits as
real CPU work for ``work_fraction`` of each 50 ms window, then sleeps for the
remainder, yielding approximately the requested % utilisation on each core.

Usage:
    python loadtest/cpu.py                  # all cores at 90% for 30 s
    python loadtest/cpu.py -s 30 -p 90      # explicit defaults
    python loadtest/cpu.py -s 60 -p 50 -c 2  # 2 cores at 50% for 60 s
    ./loadtest/cpu.py --help

Ctrl-C / SIGTERM terminates all worker processes cleanly via the finally block.
"""
from __future__ import annotations

import argparse
import multiprocessing
import os
import signal
import sys
import time


# ---------------------------------------------------------------------------
# Worker (runs in a child process)
# ---------------------------------------------------------------------------

def _worker(target_pct: float, duration_s: float, interval: float = 0.05) -> None:
    """Busy-loop at *target_pct*% per duty-cycle *interval* for *duration_s* s.

    Args:
        target_pct: Target CPU utilisation per core (0–100).
        duration_s: How long to run in seconds.
        interval: Duty-cycle window in seconds (default 50 ms).
    """
    # Workers ignore SIGINT so the parent can handle Ctrl-C cleanly.
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    work_t = max(0.001, interval * target_pct / 100.0)
    sleep_t = interval - work_t
    end_ts = time.monotonic() + duration_s

    # Leibniz series:  pi/4 = 1 - 1/3 + 1/5 - 1/7 + …
    # Accumulating the partial sum keeps the compiler from optimising it away.
    k = 0
    sign = 1.0
    acc = 0.0

    while time.monotonic() < end_ts:
        burst_end = time.monotonic() + work_t
        while time.monotonic() < burst_end:
            acc += sign / (2.0 * k + 1.0)
            sign = -sign
            k += 1
        if sleep_t > 0.001:
            time.sleep(sleep_t)

    # Emit acc so the loop cannot be optimised away (not that CPython would).
    _ = acc


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    """Parse arguments, spawn worker processes, clean up on exit."""
    parser = argparse.ArgumentParser(
        description="Stress CPU cores to a target utilisation percentage.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ./loadtest/cpu.py -s 30 -p 90       # all cores at 90% for 30 s\n"
            "  ./loadtest/cpu.py -s 60 -p 50 -c 2  # 2 cores at 50% for 60 s\n"
        ),
    )
    parser.add_argument(
        "-s", "--seconds",
        type=float,
        default=30.0,
        metavar="N",
        help="Duration to run in seconds (default: 30)",
    )
    parser.add_argument(
        "-p", "--percent",
        type=float,
        default=90.0,
        metavar="N",
        help="Target CPU %% per core, 1–100 (default: 90)",
    )
    parser.add_argument(
        "-c", "--cores",
        type=int,
        default=0,
        metavar="N",
        help="Number of cores to stress (default: all logical cores)",
    )
    args = parser.parse_args()

    if not (1.0 <= args.percent <= 100.0):
        sys.exit("error: --percent must be between 1 and 100")
    if args.seconds <= 0:
        sys.exit("error: --seconds must be positive")

    n_cores = args.cores if args.cores > 0 else (os.cpu_count() or 1)

    print(
        f"[cpu] Stressing {n_cores} core(s) to ~{args.percent:.0f}% "
        f"for {args.seconds:.0f} s — Ctrl-C to abort.",
        flush=True,
    )

    processes: list[multiprocessing.Process] = []
    exit_code = 0

    try:
        for _ in range(n_cores):
            p = multiprocessing.Process(
                target=_worker,
                args=(args.percent, args.seconds),
                daemon=True,
            )
            p.start()
            processes.append(p)

        # Block until all workers finish their allotted time.
        for p in processes:
            p.join()

        print(
            f"[cpu] Finished — {n_cores} core(s) held at ~{args.percent:.0f}% "
            f"for {args.seconds:.0f} s.",
            flush=True,
        )

    except KeyboardInterrupt:
        print("\n[cpu] Interrupted — stopping workers…", flush=True)
        exit_code = 130  # conventional SIGINT exit code

    finally:
        for p in processes:
            if p.is_alive():
                p.terminate()
        for p in processes:
            p.join(timeout=3.0)
        print("[cpu] All workers stopped.", flush=True)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
