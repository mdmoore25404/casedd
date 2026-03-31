"""Tests for CASEDD config loading from environment variables."""

from __future__ import annotations

from pathlib import Path

from casedd.config import load_config


def test_speedtest_passive_env_true(monkeypatch: object, tmp_path: Path) -> None:
    """`CASEDD_SPEEDTEST_PASSIVE=1` enables passive speedtest mode."""
    monkeypatch_obj = monkeypatch
    monkeypatch_obj.setenv("CASEDD_CONFIG", str(tmp_path / "missing.yaml"))
    monkeypatch_obj.setenv("CASEDD_SPEEDTEST_PASSIVE", "1")

    cfg = load_config()

    assert cfg.speedtest_passive is True


def test_speedtest_passive_env_false(monkeypatch: object, tmp_path: Path) -> None:
    """`CASEDD_SPEEDTEST_PASSIVE=0` disables passive speedtest mode."""
    monkeypatch_obj = monkeypatch
    monkeypatch_obj.setenv("CASEDD_CONFIG", str(tmp_path / "missing.yaml"))
    monkeypatch_obj.setenv("CASEDD_SPEEDTEST_PASSIVE", "0")

    cfg = load_config()

    assert cfg.speedtest_passive is False
