"""Regression tests for scripts/fb_unblank_daemon.py input-device filtering.

Background: on multi-GPU hosts, each GPU's HDMI/DisplayPort outputs expose an
ALSA HDMI-audio "jack sense" pseudo input device (e.g. ``HDA NVidia
HDMI/DP,pcm=3``) that only reports ``EV_SW`` (switch) capabilities -- no
real keys, relative, or absolute pointer events. These devices fire
spontaneous switch events whenever a display link renegotiates (including,
apparently, during normal operation with no user present).

``fb_unblank_daemon.py`` originally treated *any* readable event on *any*
``/dev/input/event*`` node as "local user activity", unblanking the display
and re-enabling the kernel text console (showing the login prompt) whenever
one of these jack-sense devices fired -- with no real user interaction. On
a dual-GPU host this produced roughly a dozen extra false-activity sources.

The fix filters candidate devices via ``evdev`` capabilities and only
treats devices exposing ``EV_KEY``, ``EV_REL``, or ``EV_ABS`` (real
keyboard/mouse/touch input) as genuine activity sources. These tests lock
that filtering behaviour in using mocked ``evdev.InputDevice`` capabilities
so they run without any real input hardware.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "fb_unblank_daemon.py"


def _load_fb_unblank_module() -> types.ModuleType:
    """Import scripts/fb_unblank_daemon.py as a standalone module for testing."""
    spec = importlib.util.spec_from_file_location("fb_unblank_daemon", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_fake_device(caps: dict[int, object]) -> MagicMock:
    """Build a MagicMock standing in for an evdev.InputDevice instance."""
    fake = MagicMock()
    fake.capabilities.return_value = caps
    return fake


def test_jack_sense_only_device_is_not_real_input() -> None:
    """A device exposing only EV_SW (jack sense) must be filtered out."""
    module = _load_fb_unblank_module()
    ev_sw = module.evdev_ecodes.EV_SW
    fake_dev = _make_fake_device({ev_sw: [0]})

    with patch.object(module.evdev, "InputDevice", return_value=fake_dev):
        assert module._is_real_input_device("/dev/input/event10") is False


def test_keyboard_device_is_real_input() -> None:
    """A device exposing EV_KEY must be treated as real input."""
    module = _load_fb_unblank_module()
    ev_key = module.evdev_ecodes.EV_KEY
    fake_dev = _make_fake_device({ev_key: [1, 2, 3]})

    with patch.object(module.evdev, "InputDevice", return_value=fake_dev):
        assert module._is_real_input_device("/dev/input/event0") is True


def test_mouse_device_with_rel_is_real_input() -> None:
    """A device exposing EV_REL (relative pointer motion) is real input."""
    module = _load_fb_unblank_module()
    ev_rel = module.evdev_ecodes.EV_REL
    fake_dev = _make_fake_device({ev_rel: [0, 1]})

    with patch.object(module.evdev, "InputDevice", return_value=fake_dev):
        assert module._is_real_input_device("/dev/input/event1") is True


def test_touchscreen_device_with_abs_is_real_input() -> None:
    """A device exposing EV_ABS (absolute pointer/touch) is real input."""
    module = _load_fb_unblank_module()
    ev_abs = module.evdev_ecodes.EV_ABS
    fake_dev = _make_fake_device({ev_abs: [0, 1]})

    with patch.object(module.evdev, "InputDevice", return_value=fake_dev):
        assert module._is_real_input_device("/dev/input/event2") is True


def test_unreadable_device_fails_open_as_real_input() -> None:
    """If capability probing raises, the device is treated as real (fail open).

    This preserves the escape hatch even if a device is transiently
    unreadable or evdev raises for an unexpected reason -- we never want a
    probing failure to silently disable local-input recovery entirely.
    """
    module = _load_fb_unblank_module()

    with patch.object(module.evdev, "InputDevice", side_effect=OSError("boom")):
        assert module._is_real_input_device("/dev/input/event99") is True


def test_missing_evdev_module_fails_open() -> None:
    """If evdev is unavailable at import time, all devices are treated as real."""
    module = _load_fb_unblank_module()

    with patch.object(module, "evdev", None), patch.object(module, "evdev_ecodes", None):
        assert module._is_real_input_device("/dev/input/event0") is True


def test_open_input_devices_skips_jack_sense_nodes(tmp_path: Path) -> None:
    """`_open_input_devices` must only open devices that pass the real-input filter."""
    module = _load_fb_unblank_module()

    real_path = tmp_path / "event0"
    real_path.write_bytes(b"")
    jack_path = tmp_path / "event10"
    jack_path.write_bytes(b"")

    module.INPUT_GLOB = str(tmp_path / "event*")

    def _fake_is_real(path: str) -> bool:
        return path == str(real_path)

    with patch.object(module, "_is_real_input_device", side_effect=_fake_is_real):
        devs = module._open_input_devices()

    try:
        opened_names = {getattr(fh, "name", "") for fh in devs.values()}
        assert str(real_path) in opened_names
        assert str(jack_path) not in opened_names
    finally:
        module._close_input_devices(devs)
