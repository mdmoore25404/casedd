#!/usr/bin/env python3
"""FB unblank daemon: unblanks on local input and re-blanks after idle.

Usage: run as root (recommended via systemd). Environment vars:
- FB_BLANK_PATH: sysfs blank file (default: /sys/class/graphics/fb0/blank)
- IDLE_SECONDS: seconds of inactivity before re-blank (default: 60)
- INPUT_GLOB: glob for input devices (default: /dev/input/event*)

The daemon watches all matching input event devices and writes '0' to
`FB_BLANK_PATH` on any input activity, and writes '1' after the configured
idle period elapses.
"""
from __future__ import annotations

import glob
import os
import select
import signal
import sys
import time
from pathlib import Path
from typing import Dict, List

FB_BLANK_PATH = Path(os.environ.get("FB_BLANK_PATH", "/sys/class/graphics/fb0/blank"))
IDLE_SECONDS = int(os.environ.get("IDLE_SECONDS", "60"))
INPUT_GLOB = os.environ.get("INPUT_GLOB", "/dev/input/event*")
POLL_INTERVAL = 1.0

_running = True


def _set_blank(val: int) -> None:
    try:
        FB_BLANK_PATH.write_text(str(val))
    except Exception as exc:  # pragma: no cover - system interaction
        print(f"Failed to write {FB_BLANK_PATH}: {exc}", file=sys.stderr)


def _open_input_devices() -> Dict[int, object]:
    """Open input event devices and return mapping fd -> file object."""
    devs: Dict[int, object] = {}
    for path in glob.glob(INPUT_GLOB):
        try:
            fh = open(path, "rb", buffering=0)
        except OSError:
            continue
        fd = fh.fileno()
        devs[fd] = fh
    return devs


def _close_input_devices(devs: Dict[int, object]) -> None:
    for fh in list(devs.values()):
        try:
            fh.close()
        except Exception:
            pass
    devs.clear()


def _handle_signals(signum, frame):  # pragma: no cover - signal wiring
    global _running
    _running = False


def main() -> int:
    signal.signal(signal.SIGINT, _handle_signals)
    signal.signal(signal.SIGTERM, _handle_signals)

    if not FB_BLANK_PATH.exists():
        print(f"FB blank path {FB_BLANK_PATH} not found.", file=sys.stderr)
        return 2

    # Start blanked
    _set_blank(1)
    is_blank = True
    last_activity = time.time()

    devs = _open_input_devices()
    poller = select.poll()

    for fd in devs.keys():
        poller.register(fd, select.POLLIN)

    try:
        while _running:
            # Re-scan devices periodically to catch hotplug
            if not devs:
                devs = _open_input_devices()
                for fd in devs.keys():
                    poller.register(fd, select.POLLIN)

            events = poller.poll(int(POLL_INTERVAL * 1000))
            now = time.time()
            if events:
                # Any input event -> unblank and reset timer
                if is_blank:
                    _set_blank(0)
                    is_blank = False
                last_activity = now
                # consume data from fds to clear state
                for fd, _ev in events:
                    try:
                        _ = devs[fd].read(64)
                    except Exception:
                        pass

            # Idle check
            if not is_blank and (now - last_activity) >= IDLE_SECONDS:
                _set_blank(1)
                is_blank = True

            # periodic device refresh: unregister and reopen any dead fds
            dead_fds: List[int] = [fd for fd, fh in devs.items() if fh.closed]
            for fd in dead_fds:
                try:
                    poller.unregister(fd)
                except Exception:
                    pass
                devs.pop(fd, None)

            # Occasionally re-scan new devices
            current_paths = set(glob.glob(INPUT_GLOB))
            known_paths = {getattr(fh, 'name', '') for fh in devs.values()}
            for p in current_paths - known_paths:
                try:
                    fh = open(p, "rb", buffering=0)
                    fd = fh.fileno()
                    devs[fd] = fh
                    poller.register(fd, select.POLLIN)
                except Exception:
                    continue

    finally:
        _close_input_devices(devs)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
