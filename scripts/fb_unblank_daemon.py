#!/usr/bin/env python3
"""FB unblank daemon: unblanks on local input and re-blanks after idle.

Usage: run as root (recommended via systemd). Environment vars:
- FB_BLANK_PATH: sysfs blank file (default: /sys/class/graphics/fb0/blank)
- IDLE_SECONDS: seconds of inactivity before re-blank (default: 60)
- INPUT_GLOB: glob for input devices (default: /dev/input/event*)

The daemon watches matching input event devices and writes '0' to
`FB_BLANK_PATH` on genuine keyboard/pointer activity, and writes '1' after
the configured idle period elapses. Devices that only expose EV_SW (switch
sense) capabilities -- e.g. HDMI/DisplayPort audio jack-detect nodes, of
which multi-GPU hosts can have a dozen or more -- are ignored, since their
spontaneous switch events are not genuine local user activity.
"""
from __future__ import annotations

import glob
import os
from pathlib import Path
import select
import signal
import sys
import time

try:
    import evdev
    from evdev import ecodes as evdev_ecodes
except Exception:  # pragma: no cover - evdev is a declared dependency but fail open
    evdev = None  # type: ignore[assignment]
    evdev_ecodes = None  # type: ignore[assignment]

FB_BLANK_PATH = Path(os.environ.get("FB_BLANK_PATH", "/sys/class/graphics/fb0/blank"))
IDLE_SECONDS = int(os.environ.get("IDLE_SECONDS", "60"))
INPUT_GLOB = os.environ.get("INPUT_GLOB", "/dev/input/event*")
POLL_INTERVAL = 1.0

# Event types considered genuine local-user activity: real keys, mouse/trackpad
# relative motion, and touchscreen/absolute-pointer motion. Deliberately
# excludes EV_SW (jack/lid/switch sense) -- multi-GPU hosts expose one
# HDMI/DisplayPort audio jack-sense pseudo-input device per output (e.g. two
# GPUs can add a dozen "HDA NVidia HDMI/DP" / "HD-Audio Generic HDMI/DP"
# devices), and these fire spontaneous switch events whenever a display link
# renegotiates. Treating those as user activity caused the display to
# spuriously unblank and show the console login prompt with no one present.
_REAL_INPUT_EVENT_TYPES = ("EV_KEY", "EV_REL", "EV_ABS")

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


def _is_real_input_device(path: str) -> bool:
    """Return True if `path` reports genuine keyboard/pointer capabilities.

    Excludes pseudo-input devices that only support EV_SW (switch sense),
    such as HDMI/DisplayPort audio jack-detect nodes -- see the module-level
    comment above `_REAL_INPUT_EVENT_TYPES` for the full rationale. Fails
    open (treats the device as real) if capabilities cannot be determined,
    so a missing/broken evdev never disables the escape hatch entirely.
    """
    if evdev is None or evdev_ecodes is None:
        return True
    dev = None
    try:
        dev = evdev.InputDevice(path)
        caps = dev.capabilities(verbose=False)
    except Exception:
        return True
    finally:
        if dev is not None:
            try:
                dev.close()
            except Exception:
                pass
    real_type_codes = {getattr(evdev_ecodes, name) for name in _REAL_INPUT_EVENT_TYPES}
    return any(event_type in caps for event_type in real_type_codes)


def _open_input_devices() -> dict[int, object]:
    """Open real keyboard/pointer input devices, skipping jack-sense nodes."""
    devs: dict[int, object] = {}
    for path in glob.glob(INPUT_GLOB):
        if not _is_real_input_device(path):
            continue
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

    Uses a single unbuffered ``os.write`` rather than ``Path.write_text()``.
    On this host's ``console`` sysfs attribute, the kernel's raw write()
    always reports 0 bytes consumed even though the write is otherwise
    accepted. Python's buffered text I/O (``Path.write_text`` /
    ``TextIOWrapper``) treats a 0-byte write as "no progress" and retries
    forever, spinning the daemon at 100% CPU. A raw ``os.write`` call is not
    retried by the interpreter, so a 0-byte result is simply ignored here.
    """
    try:
        fb_dir = FB_BLANK_PATH.parent
        console_path = fb_dir / "console"
        if console_path.exists():
            fd = os.open(console_path, os.O_WRONLY)
            try:
                os.write(fd, b"1" if enable else b"0")
            finally:
                os.close(fd)
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
                if not _is_real_input_device(p):
                    continue
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
