"""Unit tests for emergency key-exit watcher diagnostics."""

from __future__ import annotations

import asyncio
import errno
import select

from casedd.emergency_exit import EmergencyExitWatcher


def test_refresh_logs_permission_denied_once(monkeypatch, caplog) -> None:
    """Permission-denied device open is logged once per path."""
    watcher = EmergencyExitWatcher(
        input_glob="/dev/input/event*",
        shutdown_event=asyncio.Event(),
    )
    poller = select.poll()
    open_fds: dict[int, str] = {}

    monkeypatch.setattr(watcher, "_resolve_input_paths", lambda: {"/dev/input/event3"})

    def _deny(_path: str, _flags: int) -> int:
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr("casedd.emergency_exit.os.open", _deny)

    caplog.set_level("WARNING")
    watcher._refresh_device_fds(poller, open_fds)
    watcher._refresh_device_fds(poller, open_fds)

    records = [r.message for r in caplog.records if "permission denied" in r.message.lower()]
    assert len(records) == 1


def test_refresh_logs_other_open_error(monkeypatch, caplog) -> None:
    """Non-permission open errors include the exception details."""
    watcher = EmergencyExitWatcher(
        input_glob="/dev/input/event*",
        shutdown_event=asyncio.Event(),
    )
    poller = select.poll()
    open_fds: dict[int, str] = {}

    monkeypatch.setattr(watcher, "_resolve_input_paths", lambda: {"/dev/input/event99"})

    def _fail(_path: str, _flags: int) -> int:
        raise OSError(errno.ENOENT, "No such file or directory")

    monkeypatch.setattr("casedd.emergency_exit.os.open", _fail)

    caplog.set_level("WARNING")
    watcher._refresh_device_fds(poller, open_fds)

    assert any("No such file or directory" in r.message for r in caplog.records)
