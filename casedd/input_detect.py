"""Local input device detection utilities.

Provide a small best-effort API to detect whether a keyboard or mouse is
physically attached. This is intentionally lightweight: it prefers the
`evdev` package when available for capability inspection, and falls back to a
simple presence check of `/dev/input/event*` when not.

Public API:
    - `has_local_keyboard_or_mouse()` -> bool
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

try:  # pragma: no cover - runtime dependency
    from evdev import InputDevice
    from evdev import ecodes as e
    _HAVE_EVDEV = True
except Exception:  # pragma: no cover - runtime dependency
    _HAVE_EVDEV = False


def _iter_event_paths() -> Iterable[Path]:
    yield from Path("/dev/input").glob("event*")


def _check_with_evdev(path: Path) -> bool:
    try:
        dev = InputDevice(str(path))
        caps = dev.capabilities()
    except Exception:
        return False

    # KEY capability indicates keyboards/buttons; REL/ABS axes or BTN_MOUSE
    # imply a pointing device.
    if e.EV_KEY in caps:
        return True
    return bool(e.EV_REL in caps or e.EV_ABS in caps)


def has_local_keyboard_or_mouse() -> bool:
    """Return True if any attached input device looks like a keyboard/mouse.

    Uses `evdev` for capability checks when present; otherwise returns True
    if any `/dev/input/event*` device exists. This is conservative and
    intended only for a boot-time heuristic to decide whether CASEDD should
    take ownership of the primary display.
    """
    paths = list(_iter_event_paths())
    if not paths:
        return False

    if not _HAVE_EVDEV:
        # evdev not available — assume presence of any event device means
        # there's local input hardware attached.
        return True

    return any(_check_with_evdev(p) for p in paths)
