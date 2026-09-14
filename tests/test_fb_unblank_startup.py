"""Regression tests for fb_unblank_daemon.py panel-power startup behaviour.

Background: the daemon's original design blanked (powered off) the
framebuffer panel by default at startup and only powered it back on when
genuine local input was detected -- see test_fb_unblank_input_filter.py for
the related jack-sense filtering fix. Once that filtering fix removed the
constant stream of false activity events from HDMI/DisplayPort audio
jack-sense pseudo-devices, the panel legitimately stayed powered off
forever on a headless host with no real keyboard/mouse ever touching it --
CASEDD's own rendered frames were never visible because the panel itself
was off (DPMS-style blank), not because of anything wrong with CASEDD.

CASEDD is meant to own the physical display at all times. The escape
hatch's job is only to toggle the kernel console overlay on top of
CASEDD's frames when a human needs to physically use the machine -- it
must never power the panel off by default. These tests lock in the fixed
startup behaviour: the panel (blank) is always forced on at startup, and
the console overlay starts hidden so CASEDD's frames are visible
immediately.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import threading
import time
import types
from unittest.mock import patch

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


def _start_and_sample(
    module: types.ModuleType, blank_path: Path, console_path: Path, settle_seconds: float = 0.2
) -> tuple[bytes, bytes]:
    """Start the daemon loop, sample blank/console shortly after startup.

    Samples state *while the loop is still running* (before stopping it),
    since the daemon's shutdown ``finally`` block intentionally re-enables
    the console on exit to hand control back to a human -- that shutdown
    behaviour is unrelated to the startup/idle behaviour under test here.
    """
    thread = threading.Thread(target=module._run, daemon=True)
    thread.start()
    time.sleep(settle_seconds)
    blank_value = blank_path.read_bytes()
    console_value = console_path.read_bytes()
    module._running = False
    thread.join(timeout=5)
    assert not thread.is_alive(), "daemon _run() did not exit after _running=False"
    return blank_value, console_value


def test_panel_is_unblanked_at_startup_regardless_of_prior_state(tmp_path: Path) -> None:
    """The panel must be powered on (blank='0') at startup, every time.

    Regression guard for the reported "nothing on the screen at all" bug:
    once jack-sense false-activity was filtered out, the daemon's old
    default-blanked startup state left the panel permanently off.
    """
    module = _load_fb_unblank_module()
    fb_dir = tmp_path / "fb0"
    fb_dir.mkdir()
    blank_path = fb_dir / "blank"
    blank_path.write_text("1", encoding="utf-8")  # simulate a prior blanked state
    console_path = fb_dir / "console"
    console_path.write_text("1", encoding="utf-8")  # simulate a prior console-shown state

    module.FB_BLANK_PATH = blank_path
    module.INPUT_GLOB = str(tmp_path / "no-such-input-*")
    module.IDLE_SECONDS = 9999
    module.FB_BLANK_ON_IDLE = False
    module._running = True

    with patch.object(module, "_write_vt_cursor"):
        blank_value, console_value = _start_and_sample(module, blank_path, console_path)

    assert blank_value == b"0"
    assert console_value == b"0"



def test_panel_stays_unblanked_after_idle_timeout_by_default(tmp_path: Path) -> None:
    """With FB_BLANK_ON_IDLE unset (default), idle must not re-blank the panel.

    Only the console overlay should be hidden again on idle -- the panel
    itself must remain powered on so CASEDD's frames stay visible.
    """
    module = _load_fb_unblank_module()
    fb_dir = tmp_path / "fb0"
    fb_dir.mkdir()
    blank_path = fb_dir / "blank"
    blank_path.write_text("0", encoding="utf-8")
    console_path = fb_dir / "console"
    console_path.write_text("0", encoding="utf-8")

    module.FB_BLANK_PATH = blank_path
    module.INPUT_GLOB = str(tmp_path / "no-such-input-*")
    module.IDLE_SECONDS = 0  # idle immediately so the idle-check path runs
    module.FB_BLANK_ON_IDLE = False
    module._running = True

    with patch.object(module, "_write_vt_cursor"):
        blank_value, _console_value = _start_and_sample(module, blank_path, console_path)

    # Even though idle timeout elapsed, the panel must still be unblanked.
    assert blank_value == b"0"
