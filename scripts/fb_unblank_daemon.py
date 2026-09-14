#!/usr/bin/env python3
"""FB escape-hatch daemon: shows the kernel console on local input.

Usage: run as root (recommended via systemd). Environment vars:
- FB_BLANK_PATH: sysfs blank file (default: /sys/class/graphics/fb0/blank)
- IDLE_SECONDS: seconds of inactivity before hiding the console again
  (default: 60)
- INPUT_GLOB: glob for input devices (default: /dev/input/event*)
- FB_BLANK_ON_IDLE: legacy opt-in (default: off, see below)

CASEDD owns the physical display at all times: the framebuffer panel is
kept powered on (unblanked) so CASEDD's own rendered frames are always
visible. This daemon's only job is the "escape hatch" -- on genuine local
keyboard/mouse/touch activity it enables the kernel framebuffer console
(sysfs ``console`` attribute) so a human physically at the machine can use
it as an actual PC (login prompt, etc.), overlaying CASEDD's frames. After
``IDLE_SECONDS`` of no further activity it disables the console again so
CASEDD's frames become visible once more.

Devices that only expose EV_SW (switch sense) capabilities -- e.g.
HDMI/DisplayPort audio jack-detect nodes, of which multi-GPU hosts can have
a dozen or more -- are ignored, since their spontaneous switch events are
not genuine local user activity and previously caused the console to flash
on for no reason.

Legacy full-panel blanking (``FB_BLANK_PATH`` = 1, powering off the panel
entirely rather than just hiding/showing the console) is disabled by
default because it hides CASEDD's own display, which is never desired on a
headless case-display host. Set ``FB_BLANK_ON_IDLE=1`` to opt back into
that legacy behaviour (the panel will power off after ``IDLE_SECONDS`` and
require new input to power back on, same as the historical default).
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
# renegotiates. Treating those as user activity caused the console to
# spuriously show with no one present.
_REAL_INPUT_EVENT_TYPES = ("EV_KEY", "EV_REL", "EV_ABS")

# When true, the daemon will attempt to enable/disable the kernel framebuffer
# console for the fb device as the escape hatch toggles.
FB_DISABLE_CONSOLE = os.environ.get("FB_DISABLE_CONSOLE", "1") not in {"0", "false", "False", ""}
# When present, this file prevents the daemon from hiding the console again
# (kept for backward compatibility with manual test workflows).
FB_KEEP_PATH = Path(os.environ.get("FB_KEEP_PATH", "/run/casedd/keep-unblank"))
# Legacy opt-in: fully power off the panel on idle instead of merely hiding
# the kernel console overlay. Default off -- see module docstring.
FB_BLANK_ON_IDLE = os.environ.get("FB_BLANK_ON_IDLE", "0") not in {"0", "false", "False", ""}

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
    return _run()


def _run() -> int:
    """Run the escape-hatch daemon loop (signal-free, for testability).

    Split out from ``main()`` so tests can drive the loop from a background
    thread without hitting Python's "signal only works in main thread"
    restriction on ``signal.signal()``.
    """
    if not FB_BLANK_PATH.exists():
        print(f"FB blank path {FB_BLANK_PATH} not found.", file=sys.stderr)
        return 2

    # CASEDD owns the physical display at all times: always ensure the panel
    # is powered on (unblanked) at startup, regardless of FB_KEEP_PATH or any
    # prior state left over from a previous run. Only the kernel console
    # overlay toggles as the escape hatch; the panel itself is never blanked
    # unless the operator explicitly opts into legacy FB_BLANK_ON_IDLE.
    _set_blank(0)
    # Start with the console overlay hidden and VT cursor hidden so CASEDD's
    # rendered frames are visible immediately, before any local input.
    _write_vt_cursor(False)
    if FB_DISABLE_CONSOLE:
        _set_console(False)
    console_active = False
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
                # Any genuine input event -> show the escape-hatch console
                # and reset the idle timer.
                if not console_active:
                    _write_vt_cursor(True)
                    if FB_DISABLE_CONSOLE:
                        _set_console(True)
                    console_active = True
                last_activity = now
                # consume data from fds to clear state
                for fd, _ev in events:
                    try:
                        _ = devs[fd].read(64)
                    except Exception:
                        pass

            # Idle check: hide the console again (and, only if the operator
            # opted into legacy behaviour, power off the panel) once idle.
            # Respect a keep-file to prevent hiding during manual tests.
            if console_active and (now - last_activity) >= IDLE_SECONDS:
                if not FB_KEEP_PATH.exists():
                    _write_vt_cursor(False)
                    if FB_DISABLE_CONSOLE:
                        _set_console(False)
                    if FB_BLANK_ON_IDLE:
                        _set_blank(1)
                    console_active = False

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
