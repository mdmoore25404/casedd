"""Process-level regression tests for emergency ESC/Q daemon termination."""

from __future__ import annotations

import errno
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

import pytest

_EVENT_STRUCT = struct.Struct("@llHHI")
_EV_KEY = 1


def _reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_pid_file(pid_path: Path, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if pid_path.exists() and pid_path.read_text(encoding="utf-8").strip():
            return
        time.sleep(0.05)
    msg = f"Timed out waiting for pid file: {pid_path}"
    raise AssertionError(msg)


def _write_key_event(event_path: Path, key_code: int) -> None:
    payload = _EVENT_STRUCT.pack(0, 0, _EV_KEY, key_code, 1)
    deadline = time.monotonic() + 5.0

    while time.monotonic() < deadline:
        try:
            fd = os.open(event_path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno == errno.ENXIO:
                time.sleep(0.05)
                continue
            raise
        try:
            _ = os.write(fd, payload)
            return
        finally:
            os.close(fd)

    msg = f"Timed out opening emergency input fifo for write: {event_path}"
    raise AssertionError(msg)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux input-event test")
@pytest.mark.parametrize(
    ("key_code", "key_name"),
    [
        (1, "ESC"),
        (16, "Q"),
    ],
)
def test_emergency_exit_key_stops_daemon_after_startup_splash(
    tmp_path: Path,
    key_code: int,
    key_name: str,
) -> None:
    """Daemon exits cleanly when ESC/Q is injected after splash timeout."""
    repo_root = Path(__file__).resolve().parents[1]
    startup_seconds = 0.4
    pid_path = tmp_path / "casedd.pid"
    log_path = tmp_path / "daemon.log"
    socket_path = tmp_path / "daemon.sock"
    config_path = tmp_path / "test-config.yaml"
    input_fifo = tmp_path / "event-emergency"
    os.mkfifo(input_fifo)

    config_path.write_text(
        "\n".join(
            [
                "log_level: INFO",
                "no_fb: true",
                "test_mode: true",
                "template: system_stats",
                f"startup_frame_seconds: {startup_seconds}",
                "refresh_rate: 2.0",
                f"http_port: {_reserve_port()}",
                f"ws_port: {_reserve_port()}",
                f"socket_path: {socket_path}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["CASEDD_CONFIG"] = str(config_path)
    env["CASEDD_PID_FILE"] = str(pid_path)
    env["CASEDD_LOG_DIR"] = str(tmp_path)
    env["CASEDD_EMERGENCY_INPUT_GLOB"] = str(input_fifo)
    env["CASEDD_EMERGENCY_EXIT_KEYS"] = "1"

    with log_path.open("w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            [sys.executable, "-m", "casedd"],
            cwd=repo_root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )

    try:
        _wait_for_pid_file(pid_path, timeout_seconds=8.0)
        time.sleep(startup_seconds + 0.6)
        assert proc.poll() is None

        _write_key_event(input_fifo, key_code)
        exit_code = proc.wait(timeout=8.0)
        assert exit_code == 0
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                _ = proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                _ = proc.wait(timeout=3.0)

    daemon_log = log_path.read_text(encoding="utf-8")
    assert f"Emergency exit key received: {key_name}" in daemon_log
    assert "Daemon shutdown complete." in daemon_log
