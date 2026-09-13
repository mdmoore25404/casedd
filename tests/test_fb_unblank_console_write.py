"""Regression tests for scripts/fb_unblank_daemon.py console-write handling.

Background: ``_set_console`` previously used ``Path.write_text()`` to toggle
the kernel framebuffer console via sysfs. On this host's ``console``
attribute file, the kernel's raw ``write()`` syscall always reports 0 bytes
consumed even though the write is otherwise accepted. Python's buffered text
I/O (``Path.write_text`` / ``io.TextIOWrapper`` / ``io.BufferedWriter``)
treats a 0-byte write as "no progress" and retries the remaining buffer
forever, which pinned ``fb_unblank_daemon.py`` at 100% CPU indefinitely and
made ``systemctl stop`` on the wrapping service hang.

The fix uses a single unbuffered ``os.write()`` call, which is never
retried by the interpreter regardless of its return value. These tests lock
that behaviour in for both the standalone daemon script and the equivalent
console-restore path in ``casedd/daemon.py``.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import types
from unittest.mock import patch

from casedd.config import Config
from casedd.daemon import Daemon

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


def test_fb_unblank_source_does_not_use_buffered_write_for_console() -> None:
    """``_set_console`` must not use ``Path.write_text`` / buffered writes.

    Regression guard: buffered writes retry indefinitely on this sysfs
    attribute's always-0 write() return, hanging the daemon at 100% CPU.
    """
    source = _SCRIPT_PATH.read_text(encoding="utf-8")
    set_console_body = source.split("def _set_console(", maxsplit=1)[1].split(
        "\ndef ", maxsplit=1
    )[0]

    assert "console_path.write_text(" not in set_console_body
    assert "os.write(" in set_console_body


def test_daemon_source_does_not_use_buffered_write_for_console() -> None:
    """``Daemon._restore_console_and_cursor`` must not use buffered writes."""
    source = (_REPO_ROOT / "casedd" / "daemon.py").read_text(encoding="utf-8")
    method_body = source.split(
        "def _restore_console_and_cursor(", maxsplit=1
    )[1].split("\n    def ", maxsplit=1)[0]

    assert "console_path.write_text(" not in method_body
    assert "os.write(" in method_body


def test_fb_unblank_set_console_writes_expected_byte(tmp_path: Path) -> None:
    """``_set_console`` writes '1'/'0' to the console file and returns fast."""
    module = _load_fb_unblank_module()
    fb_dir = tmp_path / "fb0"
    fb_dir.mkdir()
    console_path = fb_dir / "console"
    console_path.write_text("0", encoding="utf-8")
    module.FB_BLANK_PATH = fb_dir / "blank"

    module._set_console(True)
    assert console_path.read_bytes() == b"1"

    module._set_console(False)
    assert console_path.read_bytes() == b"0"


def test_fb_unblank_set_console_does_not_retry_on_zero_byte_write(tmp_path: Path) -> None:
    """A raw write() returning 0 bytes must not be retried by ``_set_console``.

    Reproduces the observed kernel behaviour (write() always reports 0 bytes
    consumed) by monkeypatching ``os.write``. The old ``Path.write_text()``
    implementation would loop forever in this scenario; the fix must call
    ``os.write`` exactly once per invocation and return regardless of result.
    """
    module = _load_fb_unblank_module()
    fb_dir = tmp_path / "fb0"
    fb_dir.mkdir()
    console_path = fb_dir / "console"
    console_path.write_text("0", encoding="utf-8")
    module.FB_BLANK_PATH = fb_dir / "blank"

    real_write = os.write
    with patch.object(module.os, "write", return_value=0) as mock_write:
        module._set_console(True)

    mock_write.assert_called_once()
    # Sanity: confirm the underlying OS call itself is well-formed (would
    # succeed against a real file) even though we forced a 0-byte result.
    fd = module.os.open(console_path, module.os.O_WRONLY)
    try:
        assert real_write(fd, b"1") == 1
    finally:
        module.os.close(fd)


def test_daemon_restore_console_does_not_retry_on_zero_byte_write(tmp_path: Path) -> None:
    """``Daemon._restore_console_and_cursor`` must not hang on a 0-byte write.

    Mirrors the fb_unblank_daemon regression test above for the equivalent
    shutdown-path code in the main daemon, which previously could hang
    ``systemctl stop``/host reboot for the same reason.
    """
    daemon = Daemon(Config())
    fb_class_dir = tmp_path / "sys_class_graphics" / "fb0"
    fb_class_dir.mkdir(parents=True)
    console_path = fb_class_dir / "console"
    console_path.write_text("0", encoding="utf-8")

    panel = types.SimpleNamespace(fb_device=Path("/dev/fb0"), name="primary")

    def _fake_path(arg: str) -> Path:
        if arg == "/sys/class/graphics/fb0":
            return fb_class_dir
        return Path(arg)

    with (
        patch("casedd.daemon.Path", side_effect=_fake_path),
        patch("casedd.daemon.os.write", return_value=0) as mock_write,
    ):
        daemon._restore_console_and_cursor([panel])  # type: ignore[list-item]

    mock_write.assert_called_once()
