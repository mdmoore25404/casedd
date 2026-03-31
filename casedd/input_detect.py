"""Local input device detection utilities.

Provide a small best-effort API to detect whether a keyboard or mouse is
physically attached. This is intentionally lightweight: it prefers the
`evdev` package when available for capability inspection, and falls back to a
simple presence check of `/dev/input/event*` when not.

Public API:
    - `has_local_keyboard_or_mouse()` -> bool
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

try:  # pragma: no cover - runtime dependency
    from evdev import InputDevice
    from evdev import ecodes as e
    _HAVE_EVDEV = True
except Exception:  # pragma: no cover - runtime dependency
    _HAVE_EVDEV = False


_KEYBOARD_MARKER_CODES: set[int] = {
    # Typical typing keys to distinguish a real keyboard from devices that
    # only expose a small set of control buttons (e.g. power/video bus).
    16,  # KEY_Q
    17,  # KEY_W
    18,  # KEY_E
    30,  # KEY_A
    31,  # KEY_S
    32,  # KEY_D
    44,  # KEY_Z
    45,  # KEY_X
    46,  # KEY_C
    57,  # KEY_SPACE
    28,  # KEY_ENTER
}
_MOUSE_BUTTON_CODES: set[int] = {
    272,  # BTN_LEFT
    273,  # BTN_RIGHT
    274,  # BTN_MIDDLE
}
_TOUCH_BUTTON_CODES: set[int] = {
    330,  # BTN_TOUCH
    325,  # BTN_TOOL_FINGER
    320,  # BTN_TOOL_PEN
}
_REL_POINTER_CODES: set[int] = {
    0,  # REL_X
    1,  # REL_Y
}
_ABS_POINTER_CODES: set[int] = {
    0,   # ABS_X
    1,   # ABS_Y
    53,  # ABS_MT_POSITION_X
    54,  # ABS_MT_POSITION_Y
}
_HUMAN_INPUT_NAME_TOKENS: tuple[str, ...] = (
    "keyboard",
    "kbd",
    "mouse",
    "touchpad",
    "trackpoint",
    "trackball",
    "joystick",
    "gamepad",
    "tablet",
)


def _iter_event_paths() -> Iterable[Path]:
    yield from Path("/dev/input").glob("event*")


def _extract_codes(caps: Mapping[int, object], event_type: int) -> set[int]:
    """Extract event codes for one event type from an evdev capabilities map.

    Args:
        caps: Capabilities dict from ``InputDevice.capabilities()``.
        event_type: Numeric event type (e.g. EV_KEY, EV_REL).

    Returns:
        Set of integer event codes for that type.
    """
    raw_codes = caps.get(event_type)
    if raw_codes is None or not isinstance(raw_codes, list):
        return set()

    out: set[int] = set()
    for item in raw_codes:
        if isinstance(item, int):
            out.add(item)
            continue
        if isinstance(item, tuple) and item and isinstance(item[0], int):
            out.add(item[0])
    return out


def _looks_like_human_input_caps(caps: Mapping[int, object]) -> bool:
    """Decide whether capabilities correspond to human local input.

    Args:
        caps: Capabilities dict from ``InputDevice.capabilities()``.

    Returns:
        ``True`` for likely keyboard/mouse/touch input devices.
    """
    key_codes = _extract_codes(caps, e.EV_KEY)
    rel_codes = _extract_codes(caps, e.EV_REL)
    abs_codes = _extract_codes(caps, e.EV_ABS)

    has_keyboard = bool(key_codes & _KEYBOARD_MARKER_CODES)
    has_mouse = bool(key_codes & _MOUSE_BUTTON_CODES) and bool(
        rel_codes & _REL_POINTER_CODES or abs_codes & _ABS_POINTER_CODES
    )
    has_touch = bool(key_codes & _TOUCH_BUTTON_CODES) and bool(abs_codes & _ABS_POINTER_CODES)

    return has_keyboard or has_mouse or has_touch


def _read_device_name(path: Path) -> str | None:
    """Read the kernel input device name for an event node.

    Args:
        path: Event device path (e.g. ``/dev/input/event3``).

    Returns:
        Device name string, or ``None`` if unavailable.
    """
    name_path = Path("/sys/class/input") / path.name / "device" / "name"
    try:
        return name_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _check_with_sysfs_name(path: Path) -> bool:
    """Fallback name-based input detection for environments without evdev.

    Args:
        path: Event device path.

    Returns:
        ``True`` when the sysfs device name looks like local human input.
    """
    name = _read_device_name(path)
    if not name:
        return False
    lowered = name.lower()
    return any(token in lowered for token in _HUMAN_INPUT_NAME_TOKENS)


def _check_with_evdev(path: Path) -> bool:
    try:
        dev = InputDevice(str(path))
        caps = dev.capabilities(absinfo=False)
    except Exception:
        return False
    return _looks_like_human_input_caps(caps)


def has_local_keyboard_or_mouse() -> bool:
    """Return True if any attached input device looks like a keyboard/mouse.

    Uses `evdev` for capability checks when present; otherwise falls back to
    sysfs device-name heuristics. This avoids classifying non-human devices
    (e.g. power/video buttons) as local input.
    """
    paths = list(_iter_event_paths())
    if not paths:
        return False

    if not _HAVE_EVDEV:
        return any(_check_with_sysfs_name(p) for p in paths)

    return any(_check_with_evdev(p) for p in paths)
