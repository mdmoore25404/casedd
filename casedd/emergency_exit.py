"""Emergency key-exit watcher for recovering tty control.

This module listens to Linux input event devices and requests daemon shutdown
when either ESC or Q is pressed. It is intended as a last-resort recovery path
so operators can quickly stop CASEDD and regain tty access.

Public API:
    - ``EmergencyExitWatcher``
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import logging
import os
from pathlib import Path
import select
import struct
import threading
import time

_log = logging.getLogger(__name__)

_EV_KEY = 1
_KEY_ESC = 1
_KEY_Q = 16
_KEY_PRESS_VALUES: set[int] = {1, 2}
_EXIT_KEYS_BY_CODE: dict[int, str] = {
    _KEY_ESC: "ESC",
    _KEY_Q: "Q",
}
_EVENT_STRUCT = struct.Struct("@llHHI")
_POLL_INTERVAL_SECONDS = 0.25
_RESCAN_INTERVAL_SECONDS = 1.0


class EmergencyExitWatcher:
    """Watch Linux input events and request shutdown on ESC/Q key press.

    Args:
        input_glob: Glob for input event nodes (default: ``/dev/input/event*``).
        shutdown_event: Async event set when daemon shutdown is requested.
    """

    def __init__(self, *, input_glob: str, shutdown_event: asyncio.Event) -> None:
        self._input_glob = input_glob
        self._shutdown_event = shutdown_event
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._warned_open_paths: set[str] = set()

    def start(self) -> None:
        """Start the watcher thread if it is not already running."""
        if self._thread is not None and self._thread.is_alive():
            return

        self._loop = asyncio.get_running_loop()
        self._thread = threading.Thread(
            target=self._run_blocking,
            name="casedd-emergency-exit",
            daemon=True,
        )
        self._thread.start()
        _log.info("Emergency key-exit watcher enabled (%s)", self._input_glob)

    def stop(self) -> None:
        """Stop the watcher thread and wait briefly for clean exit."""
        self._stop_event.set()
        if self._thread is None:
            return
        self._thread.join(timeout=2.0)

    def _run_blocking(self) -> None:
        poller = select.poll()
        open_fds: dict[int, str] = {}
        last_scan = 0.0

        try:
            while not self._stop_event.is_set():
                now = time.monotonic()
                if now - last_scan >= _RESCAN_INTERVAL_SECONDS:
                    self._refresh_device_fds(poller, open_fds)
                    last_scan = now

                if not open_fds:
                    time.sleep(_POLL_INTERVAL_SECONDS)
                    continue

                try:
                    events = poller.poll(int(_POLL_INTERVAL_SECONDS * 1000.0))
                except OSError:
                    continue

                for fd, _flags in events:
                    if self._stop_event.is_set():
                        break
                    if self._handle_fd_events(fd):
                        return

        finally:
            for fd in list(open_fds):
                with contextlib.suppress(OSError):
                    poller.unregister(fd)
                with contextlib.suppress(OSError):
                    os.close(fd)

    def _refresh_device_fds(self, poller: select.poll, open_fds: dict[int, str]) -> None:
        known_paths = set(open_fds.values())
        current_paths = self._resolve_input_paths()

        for fd, path in list(open_fds.items()):
            if path in current_paths:
                continue
            with contextlib.suppress(OSError):
                poller.unregister(fd)
            with contextlib.suppress(OSError):
                os.close(fd)
            open_fds.pop(fd, None)

        for path in sorted(current_paths):
            if path in known_paths:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as exc:
                if path not in self._warned_open_paths:
                    self._warned_open_paths.add(path)
                    if exc.errno == errno.EACCES:
                        _log.warning(
                            "Emergency key-exit cannot read %s (permission denied). "
                            "Add the daemon user to the 'input' group or run with "
                            "equivalent device-read permissions.",
                            path,
                        )
                    else:
                        _log.warning(
                            "Emergency key-exit cannot read %s (%s)",
                            path,
                            exc,
                        )
                continue
            try:
                poller.register(fd, select.POLLIN)
            except OSError:
                with contextlib.suppress(OSError):
                    os.close(fd)
                continue
            open_fds[fd] = path

    def _resolve_input_paths(self) -> set[str]:
        pattern = self._input_glob
        if any(token in pattern for token in ("*", "?", "[")):
            if pattern.startswith("/"):
                return {str(path) for path in Path("/").glob(pattern.lstrip("/"))}
            return {str(path) for path in Path().glob(pattern)}

        node = Path(pattern)
        if node.exists():
            return {str(node)}
        return set()

    def _handle_fd_events(self, fd: int) -> bool:
        try:
            payload = os.read(fd, _EVENT_STRUCT.size * 32)
        except OSError:
            return False
        if not payload:
            return False

        if len(payload) < _EVENT_STRUCT.size:
            return False

        max_offset = len(payload) - _EVENT_STRUCT.size
        for offset in range(0, max_offset + 1, _EVENT_STRUCT.size):
            _tv_sec, _tv_usec, ev_type, code, value = _EVENT_STRUCT.unpack_from(payload, offset)
            if ev_type != _EV_KEY:
                continue
            if value not in _KEY_PRESS_VALUES:
                continue
            key_name = _EXIT_KEYS_BY_CODE.get(code)
            if key_name is None:
                continue

            _log.warning("Emergency exit key received: %s", key_name)
            loop = self._loop
            if loop is not None:
                loop.call_soon_threadsafe(self._shutdown_event.set)
            return True

        return False


def emergency_exit_enabled_from_env() -> bool:
    """Return whether emergency key exit should be enabled.

    Reads ``CASEDD_EMERGENCY_EXIT_KEYS`` (default enabled).
    """
    raw = os.environ.get("CASEDD_EMERGENCY_EXIT_KEYS", "1")
    return raw not in {"0", "false", "False", ""}


def emergency_input_glob_from_env() -> str:
    """Return input event glob used by the emergency key watcher."""
    return os.environ.get("CASEDD_EMERGENCY_INPUT_GLOB", "/dev/input/event*")
