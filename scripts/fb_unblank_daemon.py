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
from pathlib import Path
import select
import signal
import sys
import time

FB_BLANK_PATH = Path(os.environ.get("FB_BLANK_PATH", "/sys/class/graphics/fb0/blank"))
IDLE_SECONDS = int(os.environ.get("IDLE_SECONDS", "60"))
INPUT_GLOB = os.environ.get("INPUT_GLOB", "/dev/input/event*")
POLL_INTERVAL = 1.0

# When true, the daemon will attempt to disable the kernel framebuffer
# console for the fb device while the display is blanked.
FB_DISABLE_CONSOLE = os.environ.get("FB_DISABLE_CONSOLE", "1") not in {"0", "false", "False", ""}
# When present, this file prevents the daemon from re-blanking the display.
FB_KEEP_PATH = Path(os.environ.get("FB_KEEP_PATH", "/run/casedd/keep-unblank"))

_running = True


def _set_blank(val: int) -> None:
    try:
        FB_BLANK_PATH.write_text(str(val))
    except Exception as exc:  # pragma: no cover - system interaction
        print(f"Failed to write {FB_BLANK_PATH}: {exc}", file=sys.stderr)


def _open_input_devices() -> dict[int, object]:
    """Open input event devices and return mapping fd -> file object."""
    devs: dict[int, object] = {}
    for path in glob.glob(INPUT_GLOB):
        try:
            fh = open(path, "rb", buffering=0)
        except OSError:
            continue
        fd = fh.fileno()
        devs[fd] = fh
    return devs


def _write_vt_cursor(show: bool) -> None:
    """Show or hide the primary virtual terminal cursor (tty1).

    Writes the terminal escape sequence to `/dev/tty1`. Silently fails
    when `/dev/tty1` is unavailable.
    """
    seq = "\x1b[?25h" if show else "\x1b[?25l"
    # Try common virtual terminals and /dev/console as fallbacks. This is
    # best-effort: failures are ignored so the daemon remains robust.
    targets = [f"/dev/tty{i}" for i in range(1, 7)] + ["/dev/console"]
    for t in targets:
        try:
            with open(t, "wb", buffering=0) as fh:
                fh.write(seq.encode("ascii"))
        except Exception:
            continue


def _close_input_devices(devs: dict[int, object]) -> None:
    for fh in list(devs.values()):
        try:
            fh.close()
        except Exception:
            pass
    devs.clear()


def _set_console(enable: bool) -> None:
    """Enable or disable the kernel framebuffer console for the fb device.

    Writes '1' to the `console` sysfs file to enable, '0' to disable. Best-effort.
    """
    try:
        fb_dir = FB_BLANK_PATH.parent
        console_path = fb_dir / "console"
        if console_path.exists():
            console_path.write_text("1" if enable else "0")
    except Exception:
        pass


def _handle_signals(signum, frame):  # pragma: no cover - signal wiring
    global _running
    _running = False


def main() -> int:
    signal.signal(signal.SIGINT, _handle_signals)
    signal.signal(signal.SIGTERM, _handle_signals)

    if not FB_BLANK_PATH.exists():
        print(f"FB blank path {FB_BLANK_PATH} not found.", file=sys.stderr)
        return 2

    # Start state: if a keep-file exists (tests or user override), start
    # unblanked so we don't immediately hide the display. Otherwise start
    # blanked as before.
    if FB_KEEP_PATH.exists():
        _set_blank(0)
        is_blank = False
        # Start with VT cursor hidden and kernel console disabled so
        # rendered frames are not overlaid by the kernel text cursor.
        _write_vt_cursor(False)
        if FB_DISABLE_CONSOLE:
            _set_console(False)
    else:
        _set_blank(1)
        is_blank = True
        # Hide VT cursor while blanked
        _write_vt_cursor(False)
    last_activity = time.time()

    devs = _open_input_devices()
    poller = select.poll()

    for fd in devs.keys():
        poller.register(fd, select.POLLIN)

    try:
        while _running:
            # Re-scan devices periodically to catch hotplug
            if not devs:
                # No input devices currently present — avoid busy-looping by
                # sleeping for POLL_INTERVAL and rescanning.
                devs = _open_input_devices()
                if devs:
                    for fd in devs.keys():
                        poller.register(fd, select.POLLIN)
                else:
                    time.sleep(POLL_INTERVAL)
                    continue

            # Only poll when we have device fds registered.
            events = poller.poll(int(POLL_INTERVAL * 1000)) if devs else []
            now = time.time()
            if events:
                # Any input event -> unblank and reset timer
                if is_blank:
                    _set_blank(0)
                    # show VT cursor when display is unblanked
                    _write_vt_cursor(True)
                    # enable kernel console so local login/prompt works
                    if FB_DISABLE_CONSOLE:
                        _set_console(True)
                    is_blank = False
                last_activity = now
                # consume data from fds to clear state
                for fd, _ev in events:
                    try:
                        _ = devs[fd].read(64)
                    except Exception:
                        pass

            # Idle check: respect a keep-file to prevent re-blanking during tests
            if not is_blank and (now - last_activity) >= IDLE_SECONDS:
                if not FB_KEEP_PATH.exists():
                    _set_blank(1)
                    # hide VT cursor when re-blanking
                    _write_vt_cursor(False)
                    # optionally disable kernel console if configured
                    if FB_DISABLE_CONSOLE:
                        _set_console(False)
                    is_blank = True

            # periodic device refresh: unregister and reopen any dead fds
            dead_fds: list[int] = [fd for fd, fh in devs.items() if fh.closed]
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

            # Small sleep to avoid tight spinning if events keep arriving
            # rapidly (prevents runaway CPU usage on busy devices).
            time.sleep(0.01)

    finally:
        # Ensure cursor and console are put back to usable state on exit.
        try:
            _write_vt_cursor(True)
            if FB_DISABLE_CONSOLE:
                _set_console(True)
        except Exception:
            pass
        _close_input_devices(devs)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
