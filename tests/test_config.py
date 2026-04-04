"""Tests for CASEDD config loading from environment variables."""

from __future__ import annotations

from pathlib import Path

import yaml

from casedd.config import RotationEntry, load_config, save_rotation_config_to_yaml


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


def test_save_rotation_config_to_yaml_single_panel(
        monkeypatch: object,
        tmp_path: Path,
) -> None:
        """Rotation updates are persisted to top-level keys for single-panel config."""
        cfg_path = tmp_path / "casedd.yaml"
        cfg_path.write_text(
                "\n".join(
                        [
                                "template: system_stats",
                                "template_rotation:",
                                "  - apod",
                                "template_rotation_interval: 30",
                        ]
                ),
                encoding="utf-8",
        )
        monkeypatch_obj = monkeypatch
        monkeypatch_obj.setenv("CASEDD_CONFIG", str(cfg_path))

        save_rotation_config_to_yaml(
                "primary",
                ["apod", "nzbget_queue"],
                20.0,
                True,
                [
                        RotationEntry(template="apod", seconds=10),
                        RotationEntry(template="nzbget_queue", seconds=15),
                ],
        )

        loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        assert isinstance(loaded, dict)
        assert loaded.get("template_rotation_interval") == 20.0
        assert loaded.get("template_rotation_enabled") is True
        rotation = loaded.get("template_rotation")
        assert isinstance(rotation, list)
        assert rotation == [
                {"template": "apod", "seconds": 10.0, "skip_if": []},
                {"template": "nzbget_queue", "seconds": 15.0, "skip_if": []},
        ]


def test_save_rotation_config_to_yaml_panel(
        monkeypatch: object,
        tmp_path: Path,
) -> None:
        """Rotation updates are persisted into matching panel entry for multi-panel YAML."""
        cfg_path = tmp_path / "casedd.yaml"
        cfg_path.write_text(
                "\n".join(
                        [
                                "template: system_stats",
                                "panels:",
                                "  - name: primary",
                                "    template: system_stats",
                                "  - name: side",
                                "    template: sysinfo",
                                "    template_rotation:",
                                "      - apod",
                                "    template_rotation_interval: 30",
                        ]
                ),
                encoding="utf-8",
        )
        monkeypatch_obj = monkeypatch
        monkeypatch_obj.setenv("CASEDD_CONFIG", str(cfg_path))

        save_rotation_config_to_yaml(
                "side",
                ["apod", "nzbget_queue"],
                25.0,
                False,
                [
                        RotationEntry(template="apod", seconds=10),
                        RotationEntry(template="nzbget_queue", seconds=15),
                ],
        )

        loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        assert isinstance(loaded, dict)
        panels = loaded.get("panels")
        assert isinstance(panels, list)
        panel_side = next(
                panel
                for panel in panels
                if isinstance(panel, dict) and str(panel.get("name", "")) == "side"
        )
        assert panel_side["template_rotation_interval"] == 25.0
        assert panel_side["template_rotation_enabled"] is False
        assert panel_side["template_rotation"] == [
                {"template": "apod", "seconds": 10.0, "skip_if": []},
                {"template": "nzbget_queue", "seconds": 15.0, "skip_if": []},
        ]


def test_pihole_env_settings_parse(monkeypatch: object, tmp_path: Path) -> None:
        """Pi-hole env vars should map into typed config fields."""
        monkeypatch_obj = monkeypatch
        monkeypatch_obj.setenv("CASEDD_CONFIG", str(tmp_path / "missing.yaml"))
        monkeypatch_obj.setenv("CASEDD_PIHOLE_BASE_URL", "https://pi.hole")
        monkeypatch_obj.setenv("CASEDD_PIHOLE_API_TOKEN", "token")
        monkeypatch_obj.setenv("CASEDD_PIHOLE_PASSWORD", "pw")
        monkeypatch_obj.setenv("CASEDD_PIHOLE_SESSION_SID", "sid")
        monkeypatch_obj.setenv("CASEDD_PIHOLE_TIMEOUT", "6")
        monkeypatch_obj.setenv("CASEDD_PIHOLE_VERIFY_TLS", "0")
        monkeypatch_obj.setenv("CASEDD_PIHOLE_INTERVAL", "15")

        cfg = load_config()

        assert cfg.pihole_base_url == "https://pi.hole"
        assert cfg.pihole_api_token == "token"
        assert cfg.pihole_password == "pw"
        assert cfg.pihole_session_sid == "sid"
        assert cfg.pihole_timeout == 6.0
        assert cfg.pihole_verify_tls is False
        assert cfg.pihole_interval == 15.0


def test_synology_env_settings_parse(monkeypatch: object, tmp_path: Path) -> None:
        """Synology env vars should map into typed config fields."""
        monkeypatch_obj = monkeypatch
        monkeypatch_obj.setenv("CASEDD_CONFIG", str(tmp_path / "missing.yaml"))
        monkeypatch_obj.setenv("CASEDD_SYNOLOGY_HOST", "https://nas.local:5001")
        monkeypatch_obj.setenv("CASEDD_SYNOLOGY_USERNAME", "admin")
        monkeypatch_obj.setenv("CASEDD_SYNOLOGY_PASSWORD", "secret")
        monkeypatch_obj.setenv("CASEDD_SYNOLOGY_SID", "sid-abc")
        monkeypatch_obj.setenv("CASEDD_SYNOLOGY_INTERVAL", "30")
        monkeypatch_obj.setenv("CASEDD_SYNOLOGY_TIMEOUT", "9")
        monkeypatch_obj.setenv("CASEDD_SYNOLOGY_VERIFY_TLS", "0")
        monkeypatch_obj.setenv("CASEDD_SYNOLOGY_VOLUME_EXCLUDE_REGEX", "(backup)")
        monkeypatch_obj.setenv("CASEDD_SYNOLOGY_USER_EXCLUDE_REGEX", "(guest)")
        monkeypatch_obj.setenv("CASEDD_SYNOLOGY_SURVEILLANCE_ENABLED", "1")
        monkeypatch_obj.setenv("CASEDD_SYNOLOGY_SURVEILLANCE_MAX_CAMERAS", "3")
        monkeypatch_obj.setenv("CASEDD_SYNOLOGY_CAMERA_SNAPSHOT_ENABLED", "1")
        monkeypatch_obj.setenv("CASEDD_SYNOLOGY_CAMERA_SNAPSHOT_WIDTH", "800")
        monkeypatch_obj.setenv("CASEDD_SYNOLOGY_CAMERA_SNAPSHOT_HEIGHT", "450")
        monkeypatch_obj.setenv("CASEDD_SYNOLOGY_DSM_UPDATES_ENABLED", "1")

        cfg = load_config()

        assert cfg.synology_host == "https://nas.local:5001"
        assert cfg.synology_username == "admin"
        assert cfg.synology_password == "secret"
        assert cfg.synology_sid == "sid-abc"
        assert cfg.synology_interval == 30.0
        assert cfg.synology_timeout == 9.0
        assert cfg.synology_verify_tls is False
        assert cfg.synology_volume_exclude_regex == "(backup)"
        assert cfg.synology_user_exclude_regex == "(guest)"
        assert cfg.synology_surveillance_enabled is True
        assert cfg.synology_surveillance_max_cameras == 3
        assert cfg.synology_camera_snapshot_enabled is True
        assert cfg.synology_camera_snapshot_width == 800
        assert cfg.synology_camera_snapshot_height == 450
        assert cfg.synology_dsm_updates_enabled is True


def test_invokeai_env_settings_parse(monkeypatch: object, tmp_path: Path) -> None:
        """InvokeAI env vars should map into typed config fields."""
        monkeypatch_obj = monkeypatch
        monkeypatch_obj.setenv("CASEDD_CONFIG", str(tmp_path / "missing.yaml"))
        monkeypatch_obj.setenv("CASEDD_INVOKEAI_BASE_URL", "http://bandit:9090")
        monkeypatch_obj.setenv("CASEDD_INVOKEAI_API_TOKEN", "token")
        monkeypatch_obj.setenv("CASEDD_INVOKEAI_INTERVAL", "11")
        monkeypatch_obj.setenv("CASEDD_INVOKEAI_TIMEOUT", "7")
        monkeypatch_obj.setenv("CASEDD_INVOKEAI_VERIFY_TLS", "0")

        cfg = load_config()

        assert cfg.invokeai_base_url == "http://bandit:9090"
        assert cfg.invokeai_api_token == "token"
        assert cfg.invokeai_interval == 11.0
        assert cfg.invokeai_timeout == 7.0
        assert cfg.invokeai_verify_tls is False


def test_os_updates_env_settings_parse(monkeypatch: object, tmp_path: Path) -> None:
        """OS updates env vars should map into typed config fields."""
        monkeypatch_obj = monkeypatch
        monkeypatch_obj.setenv("CASEDD_CONFIG", str(tmp_path / "missing.yaml"))
        monkeypatch_obj.setenv("CASEDD_OS_UPDATES_INTERVAL", "120")
        monkeypatch_obj.setenv("CASEDD_OS_UPDATES_MANAGER", "apt")

        cfg = load_config()

        assert cfg.os_updates_interval == 120.0
        assert cfg.os_updates_manager == "apt"


def test_vms_env_settings_parse(monkeypatch: object, tmp_path: Path) -> None:
        """VM getter env vars should map into typed config fields."""
        monkeypatch_obj = monkeypatch
        monkeypatch_obj.setenv("CASEDD_CONFIG", str(tmp_path / "missing.yaml"))
        monkeypatch_obj.setenv("CASEDD_VMS_INTERVAL", "15")
        monkeypatch_obj.setenv("CASEDD_VMS_PASSIVE", "1")
        monkeypatch_obj.setenv("CASEDD_VMS_COMMAND", "/usr/bin/virsh")
        monkeypatch_obj.setenv("CASEDD_VMS_MAX_ITEMS", "12")

        cfg = load_config()

        assert cfg.vms_interval == 15.0
        assert cfg.vms_passive is True
        assert cfg.vms_command == "/usr/bin/virsh"
        assert cfg.vms_max_items == 12


def test_containers_env_settings_parse(monkeypatch: object, tmp_path: Path) -> None:
        """Containers env vars should map into typed config fields."""
        monkeypatch_obj = monkeypatch
        monkeypatch_obj.setenv("CASEDD_CONFIG", str(tmp_path / "missing.yaml"))
        monkeypatch_obj.setenv("CASEDD_CONTAINERS_INTERVAL", "11")
        monkeypatch_obj.setenv("CASEDD_CONTAINERS_RUNTIME", "podman")
        monkeypatch_obj.setenv("CASEDD_CONTAINERS_MAX_ITEMS", "25")

        cfg = load_config()

        assert cfg.containers_interval == 11.0
        assert cfg.containers_runtime == "podman"
        assert cfg.containers_max_items == 25


def test_servarr_env_settings_parse(monkeypatch: object, tmp_path: Path) -> None:
        """Radarr/Sonarr env vars should map into typed config fields."""
        monkeypatch_obj = monkeypatch
        monkeypatch_obj.setenv("CASEDD_CONFIG", str(tmp_path / "missing.yaml"))
        monkeypatch_obj.setenv("CASEDD_RADARR_BASE_URL", "https://radarr.local")
        monkeypatch_obj.setenv("CASEDD_RADARR_API_KEY", "radarr-key")
        monkeypatch_obj.setenv("CASEDD_RADARR_INTERVAL", "20")
        monkeypatch_obj.setenv("CASEDD_RADARR_TIMEOUT", "5")
        monkeypatch_obj.setenv("CASEDD_RADARR_CALENDAR_DAYS", "10")
        monkeypatch_obj.setenv("CASEDD_RADARR_VERIFY_TLS", "0")
        monkeypatch_obj.setenv("CASEDD_SONARR_BASE_URL", "https://sonarr.local")
        monkeypatch_obj.setenv("CASEDD_SONARR_API_KEY", "sonarr-key")
        monkeypatch_obj.setenv("CASEDD_SONARR_INTERVAL", "25")
        monkeypatch_obj.setenv("CASEDD_SONARR_TIMEOUT", "6")
        monkeypatch_obj.setenv("CASEDD_SONARR_CALENDAR_DAYS", "9")
        monkeypatch_obj.setenv("CASEDD_SONARR_VERIFY_TLS", "0")

        cfg = load_config()

        assert cfg.radarr_base_url == "https://radarr.local"
        assert cfg.radarr_api_key == "radarr-key"
        assert cfg.radarr_interval == 20.0
        assert cfg.radarr_timeout == 5.0
        assert cfg.radarr_calendar_days == 10
        assert cfg.radarr_verify_tls is False
        assert cfg.sonarr_base_url == "https://sonarr.local"
        assert cfg.sonarr_api_key == "sonarr-key"
        assert cfg.sonarr_interval == 25.0
        assert cfg.sonarr_timeout == 6.0
        assert cfg.sonarr_calendar_days == 9
        assert cfg.sonarr_verify_tls is False


def test_speedtest_cache_yaml_settings_parse(
        monkeypatch: object,
        tmp_path: Path,
) -> None:
        """Speedtest cache YAML settings map into typed config fields."""
        cfg_path = tmp_path / "casedd.yaml"
        cfg_path.write_text(
                "\n".join(
                        [
                                "speedtest_cache_path: run/custom-speedtest-cache.json",
                                "speedtest_cache_max_age_hours: 12",
                        ]
                ),
                encoding="utf-8",
        )

        monkeypatch_obj = monkeypatch
        monkeypatch_obj.setenv("CASEDD_CONFIG", str(cfg_path))

        cfg = load_config()

        assert cfg.speedtest_cache_path == Path("run/custom-speedtest-cache.json")
        assert cfg.speedtest_cache_max_age_hours == 12.0
