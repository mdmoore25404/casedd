"""Tests for local input-device detection heuristics."""

from __future__ import annotations

from pathlib import Path

from casedd import input_detect


def test_caps_keyboard_detected() -> None:
    """Typing-key capabilities are detected as human input."""
    caps: dict[int, object] = {
        1: [16, 30, 44],  # EV_KEY: Q, A, Z
    }
    assert input_detect._looks_like_human_input_caps(caps)


def test_caps_power_button_not_detected() -> None:
    """Power-button-only devices are not treated as keyboard/mouse."""
    caps: dict[int, object] = {
        1: [116],  # EV_KEY: KEY_POWER
    }
    assert not input_detect._looks_like_human_input_caps(caps)


def test_caps_mouse_detected() -> None:
    """Mouse button + pointer axis capabilities are detected as local input."""
    caps: dict[int, object] = {
        1: [272],  # EV_KEY: BTN_LEFT
        2: [0, 1],  # EV_REL: REL_X, REL_Y
    }
    assert input_detect._looks_like_human_input_caps(caps)


def test_no_evdev_uses_name_heuristic_true(monkeypatch) -> None:
    """Without evdev, keyboard-like sysfs names are treated as local input."""
    monkeypatch.setattr(input_detect, "_HAVE_EVDEV", False)
    monkeypatch.setattr(
        input_detect,
        "_iter_event_paths",
        lambda: iter([Path("/dev/input/event9")]),
    )
    monkeypatch.setattr(
        input_detect,
        "_check_with_sysfs_name",
        lambda _path: True,
    )

    assert input_detect.has_local_keyboard_or_mouse()


def test_no_evdev_uses_name_heuristic_false(monkeypatch) -> None:
    """Without evdev, non-human devices are ignored by name heuristic."""
    monkeypatch.setattr(input_detect, "_HAVE_EVDEV", False)
    monkeypatch.setattr(
        input_detect,
        "_iter_event_paths",
        lambda: iter([Path("/dev/input/event2")]),
    )
    monkeypatch.setattr(
        input_detect,
        "_check_with_sysfs_name",
        lambda _path: False,
    )

    assert not input_detect.has_local_keyboard_or_mouse()
