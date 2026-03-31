"""Tests for :class:`~casedd.getter_health.GetterHealthRegistry` (issue #27)."""

from __future__ import annotations

import time

from casedd.getter_health import _ERROR_LOG_THROTTLE_SEC, GetterHealthRegistry


def _registry_with(*names: str) -> GetterHealthRegistry:
    reg = GetterHealthRegistry()
    for n in names:
        reg.register(n)
    return reg


# ---------------------------------------------------------------------------
# register / snapshot
# ---------------------------------------------------------------------------


def test_register_creates_starting_entry() -> None:
    """Newly registered getter appears in snapshot with status 'inactive'."""
    reg = _registry_with("CpuGetter")
    snap = reg.snapshot()
    assert len(snap) == 1
    assert snap[0]["name"] == "CpuGetter"
    assert snap[0]["status"] == "inactive"
    assert snap[0]["error_count"] == 0


def test_register_idempotent() -> None:
    """Calling register() twice for the same name does not duplicate entries."""
    reg = GetterHealthRegistry()
    reg.register("X")
    reg.register("X")
    assert len(reg.snapshot()) == 1


def test_snapshot_sorted_by_name() -> None:
    """snapshot() returns entries sorted alphabetically by getter name."""
    reg = _registry_with("ZGetter", "AGetter", "MGetter")
    names = [e["name"] for e in reg.snapshot()]
    assert names == ["AGetter", "MGetter", "ZGetter"]


# ---------------------------------------------------------------------------
# record_success
# ---------------------------------------------------------------------------


def test_record_success_sets_ok_status() -> None:
    """After record_success, status becomes 'ok'."""
    reg = _registry_with("G")
    reg.record_success("G")
    entry = reg.snapshot()[0]
    assert entry["status"] == "ok"
    assert entry["consecutive_errors"] == 0
    assert entry["last_success_at"] is not None


def test_record_success_resets_consecutive_errors() -> None:
    """record_success resets consecutive_errors to 0."""
    reg = _registry_with("G")
    reg.record_error("G", "boom")
    reg.record_error("G", "boom2")
    reg.record_success("G")
    assert reg.snapshot()[0]["consecutive_errors"] == 0


# ---------------------------------------------------------------------------
# record_error
# ---------------------------------------------------------------------------


def test_record_error_sets_error_status() -> None:
    """After record_error, status becomes 'error'."""
    reg = _registry_with("G")
    reg.record_error("G", "connection refused")
    entry = reg.snapshot()[0]
    assert entry["status"] == "error"
    assert entry["error_count"] == 1
    assert entry["last_error_msg"] == "connection refused"


def test_record_error_increments_count() -> None:
    """Repeated record_error calls increment error_count each time."""
    reg = _registry_with("G")
    for i in range(5):
        reg.record_error("G", f"err{i}")
    assert reg.snapshot()[0]["error_count"] == 5
    assert reg.snapshot()[0]["consecutive_errors"] == 5


def test_record_error_first_call_should_log() -> None:
    """The first error should always be logged (returns True)."""
    reg = _registry_with("G")
    should_log = reg.record_error("G", "first")
    assert should_log is True


def test_record_error_throttled_on_repeat() -> None:
    """A second immediate error is suppressed (returns False)."""
    reg = _registry_with("G")
    reg.record_error("G", "first")
    should_log = reg.record_error("G", "second")
    assert should_log is False


def test_record_error_logs_after_throttle_window(monkeypatch: object) -> None:
    """After the throttle window, the next error should be logged again."""
    reg = _registry_with("G")
    reg.record_error("G", "first")

    # Advance the last-log timestamp beyond the throttle window.
    assert isinstance(monkeypatch, object)
    original_time = time.time

    future = original_time() + _ERROR_LOG_THROTTLE_SEC + 1

    # Use monkeypatch via the actual pytest fixture type.
    # We inject manually here since monkeypatch here is typed as object.
    reg._last_log_at["G"] = original_time() - _ERROR_LOG_THROTTLE_SEC - 1  # type: ignore[attr-defined]
    should_log = reg.record_error("G", "third")
    assert should_log is True
    _ = future  # suppress unused


# ---------------------------------------------------------------------------
# any_ok / all_ok
# ---------------------------------------------------------------------------


def test_any_ok_false_when_no_successes() -> None:
    reg = _registry_with("A", "B")
    assert reg.any_ok() is False


def test_any_ok_true_after_one_success() -> None:
    reg = _registry_with("A", "B")
    reg.record_success("A")
    assert reg.any_ok() is True


def test_all_ok_true_when_all_succeeded() -> None:
    reg = _registry_with("A", "B")
    reg.record_success("A")
    reg.record_success("B")
    assert reg.all_ok() is True


def test_all_ok_false_when_any_error() -> None:
    reg = _registry_with("A", "B")
    reg.record_success("A")
    reg.record_error("B", "down")
    assert reg.all_ok() is False
