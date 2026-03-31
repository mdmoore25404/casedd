"""Tests for CASEDD config loading from environment variables."""

from __future__ import annotations

from pathlib import Path

from casedd.config import RotationEntry, load_config


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


def test_template_rotation_entries_parse_from_yaml(
        monkeypatch: object,
        tmp_path: Path,
) -> None:
        """YAML template_rotation accepts per-entry seconds overrides."""
        cfg_path = tmp_path / "casedd.yaml"
        cfg_path.write_text(
                "\n".join(
                        [
                                "template: system_stats",
                                "template_rotation:",
                                "  - system_stats",
                                "  - template: apod",
                                "    seconds: 10",
                                "  - template: nzbget_queue",
                                "    seconds: 15",
                                "template_rotation_interval: 30",
                        ]
                ),
                encoding="utf-8",
        )

        monkeypatch_obj = monkeypatch
        monkeypatch_obj.setenv("CASEDD_CONFIG", str(cfg_path))

        cfg = load_config()

        assert len(cfg.template_rotation) == 3
        assert cfg.template_rotation[0] == "system_stats"
        assert isinstance(cfg.template_rotation[1], RotationEntry)
        assert cfg.template_rotation[1].template == "apod"
        assert cfg.template_rotation[1].seconds == 10.0
        assert isinstance(cfg.template_rotation[2], RotationEntry)
        assert cfg.template_rotation[2].template == "nzbget_queue"
        assert cfg.template_rotation[2].seconds == 15.0


def test_panel_template_rotation_entries_parse_from_yaml(
        monkeypatch: object,
        tmp_path: Path,
) -> None:
        """Panel-level template_rotation accepts mixed string/entry values."""
        cfg_path = tmp_path / "casedd.yaml"
        cfg_path.write_text(
                                "\n".join(
                                                [
                                                                "template: system_stats",
                                                                "panels:",
                                                                "  - name: primary",
                                                                "    template: system_stats",
                                                                "    template_rotation:",
                                                                "      - template: apod",
                                                                "        seconds: 10",
                                                                "      - nzbget_queue",
                                                ]
                                ),
                encoding="utf-8",
        )

        monkeypatch_obj = monkeypatch
        monkeypatch_obj.setenv("CASEDD_CONFIG", str(cfg_path))

        cfg = load_config()

        assert len(cfg.panels) == 1
        assert len(cfg.panels[0].template_rotation) == 2
        assert isinstance(cfg.panels[0].template_rotation[0], RotationEntry)
        assert cfg.panels[0].template_rotation[0].template == "apod"
        assert cfg.panels[0].template_rotation[0].seconds == 10.0
        assert cfg.panels[0].template_rotation[1] == "nzbget_queue"
