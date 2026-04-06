"""Tests for configurable getter poll intervals.

Covers:
- All 9 newly-configurable getter intervals present on Config with correct defaults
- Custom YAML values are parsed and respected
- load_config() reads CASEDD_<NAME>_INTERVAL env vars
- Daemon._create_getters() passes intervals to getter constructors
- Pydantic rejects zero / negative interval values
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from pydantic import ValidationError
import pytest

from casedd.config import Config, load_config
from casedd.daemon import Daemon
from casedd.data_store import DataStore
from casedd.getters.cpu import CpuGetter
from casedd.getters.disk import DiskGetter
from casedd.getters.network import NetworkGetter
from casedd.getters.sysinfo import SysinfoGetter

# -- Default values on Config --


def test_config_cpu_interval_default() -> None:
    """cpu_interval defaults to 2.0 s."""
    assert Config().cpu_interval == 2.0


def test_config_gpu_interval_default() -> None:
    """gpu_interval defaults to 5.0 s."""
    assert Config().gpu_interval == 5.0


def test_config_memory_interval_default() -> None:
    """memory_interval defaults to 2.0 s."""
    assert Config().memory_interval == 2.0


def test_config_disk_interval_default() -> None:
    """disk_interval defaults to 2.0 s."""
    assert Config().disk_interval == 2.0


def test_config_network_interval_default() -> None:
    """network_interval defaults to 2.0 s."""
    assert Config().network_interval == 2.0


def test_config_system_interval_default() -> None:
    """system_interval defaults to 10.0 s."""
    assert Config().system_interval == 10.0


def test_config_fans_interval_default() -> None:
    """fans_interval defaults to 3.0 s."""
    assert Config().fans_interval == 3.0


def test_config_net_ports_interval_default() -> None:
    """net_ports_interval defaults to 5.0 s."""
    assert Config().net_ports_interval == 5.0


def test_config_sysinfo_interval_default() -> None:
    """sysinfo_interval defaults to 30.0 s."""
    assert Config().sysinfo_interval == 30.0


# -- Custom values accepted --


def test_config_custom_intervals_accepted() -> None:
    """All 9 intervals accept custom float values."""
    cfg = Config(
        cpu_interval=1.0,
        gpu_interval=30.0,
        memory_interval=1.5,
        disk_interval=5.0,
        network_interval=1.0,
        system_interval=60.0,
        fans_interval=10.0,
        net_ports_interval=15.0,
        sysinfo_interval=120.0,
    )
    assert cfg.cpu_interval == 1.0
    assert cfg.gpu_interval == 30.0
    assert cfg.memory_interval == 1.5
    assert cfg.disk_interval == 5.0
    assert cfg.network_interval == 1.0
    assert cfg.system_interval == 60.0
    assert cfg.fans_interval == 10.0
    assert cfg.net_ports_interval == 15.0
    assert cfg.sysinfo_interval == 120.0


# -- Validation: zero / negative intervals are rejected --


@pytest.mark.parametrize(
    "field",
    [
        "cpu_interval",
        "gpu_interval",
        "memory_interval",
        "disk_interval",
        "network_interval",
        "system_interval",
        "fans_interval",
        "net_ports_interval",
        "sysinfo_interval",
    ],
)
def test_interval_must_be_positive(field: str) -> None:
    """Each interval field rejects zero and negative values (gt=0 constraint)."""
    with pytest.raises(ValidationError):
        Config(**{field: 0.0})


# -- load_config() reads env vars --


def test_load_config_cpu_interval_from_env(tmp_path: Path) -> None:
    """CASEDD_CPU_INTERVAL env var is picked up by load_config()."""
    cfg_file = tmp_path / "casedd.yaml"
    cfg_file.write_text("log_level: INFO\n")
    env = {
        **os.environ,
        "CASEDD_CONFIG": str(cfg_file),
        "CASEDD_CPU_INTERVAL": "8.0",
    }
    with patch.dict(os.environ, env, clear=True):
        cfg = load_config()
    assert cfg.cpu_interval == 8.0


def test_load_config_sysinfo_interval_from_yaml(tmp_path: Path) -> None:
    """sysinfo_interval YAML key is read by load_config()."""
    cfg_file = tmp_path / "casedd.yaml"
    cfg_file.write_text("log_level: INFO\nsysinfo_interval: 120.0\n")
    env = {**os.environ, "CASEDD_CONFIG": str(cfg_file)}
    with patch.dict(os.environ, env, clear=True):
        cfg = load_config()
    assert cfg.sysinfo_interval == 120.0


def test_load_config_env_overrides_yaml(tmp_path: Path) -> None:
    """An env var takes priority over the same key in casedd.yaml."""
    cfg_file = tmp_path / "casedd.yaml"
    cfg_file.write_text("log_level: INFO\ngpu_interval: 20.0\n")
    env = {
        **os.environ,
        "CASEDD_CONFIG": str(cfg_file),
        "CASEDD_GPU_INTERVAL": "60.0",
    }
    with patch.dict(os.environ, env, clear=True):
        cfg = load_config()
    assert cfg.gpu_interval == 60.0


# -- Getter constructors propagate intervals --


def test_cpu_getter_uses_configured_interval() -> None:
    """CpuGetter is constructed with the interval from Config."""
    store = DataStore()
    getter = CpuGetter(store, interval=4.0)
    assert getter._interval == 4.0


def test_disk_getter_uses_configured_interval() -> None:
    """DiskGetter respects the interval kwarg."""
    store = DataStore()
    getter = DiskGetter(store, mount="/", interval=7.0)
    assert getter._interval == 7.0


def test_network_getter_uses_configured_interval() -> None:
    """NetworkGetter respects the interval kwarg."""
    store = DataStore()
    getter = NetworkGetter(store, interval=3.5)
    assert getter._interval == 3.5


def test_sysinfo_getter_uses_configured_interval() -> None:
    """SysinfoGetter respects the interval kwarg."""
    store = DataStore()
    getter = SysinfoGetter(store, interval=90.0)
    assert getter._interval == 90.0


# -- Daemon._create_getters passes intervals --


def test_daemon_create_getters_propagates_intervals() -> None:
    """_create_getters() forwards all 9 new interval fields to their getters."""
    cfg = Config(
        cpu_interval=11.0,
        gpu_interval=22.0,
        memory_interval=33.0,
        disk_interval=44.0,
        network_interval=55.0,
        system_interval=66.0,
        fans_interval=77.0,
        net_ports_interval=88.0,
        sysinfo_interval=99.0,
        # Disable all other interval-related options that need external services.
        test_mode=True,
    )
    daemon = Daemon(cfg)
    # Attach a stub health registry so getters can be created.
    mock_health = MagicMock()
    mock_health.register = MagicMock()
    daemon._health = mock_health
    daemon._store = DataStore()

    getters = daemon._create_getters()

    by_type = {type(g).__name__: g for g in getters}

    assert by_type["CpuGetter"]._interval == 11.0
    assert by_type["GpuGetter"]._interval == 22.0
    assert by_type["MemoryGetter"]._interval == 33.0
    assert by_type["DiskGetter"]._interval == 44.0
    assert by_type["NetworkGetter"]._interval == 55.0
    assert by_type["SystemGetter"]._interval == 66.0
    assert by_type["FanGetter"]._interval == 77.0
    assert by_type["NetPortsGetter"]._interval == 88.0
    assert by_type["SysinfoGetter"]._interval == 99.0
