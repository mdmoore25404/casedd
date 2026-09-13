"""Tests for Tuya getter parsing.

Covers plug DPS mappings and temperature/humidity sensor mappings.
"""

from __future__ import annotations

from casedd.config import TuyaDeviceConfig
from casedd.data_store import DataStore
from casedd.getters.tuya import (
    _TUYA_SOCKET_RETRY_DELAY_SECONDS,
    _TUYA_SOCKET_RETRY_LIMIT,
    _TUYA_SOCKET_TIMEOUT_SECONDS,
    TuyaGetter,
)


def test_parse_plug_data_uses_cur_mappings() -> None:
    """Plug parser should map current/power/voltage/energy from observed DPS keys."""
    getter = TuyaGetter(DataStore(), devices=[], interval=10.0)
    device = TuyaDeviceConfig(
        device_id="demo_plug_001",
        local_key="k",
        device_type="plug",
        ip_address=None,
    )
    status = {
        "dps": {
            "18": 915,
            "19": 1112,
            "20": 1210,
            "17": 59,
        }
    }

    parsed = getter._parse_plug_data(device, status)

    assert parsed["tuya.plugs.demo_plug_001.current"] == 915.0
    assert parsed["tuya.plugs.demo_plug_001.power"] == 111.2
    assert parsed["tuya.plugs.demo_plug_001.voltage"] == 121.0
    assert parsed["tuya.plugs.demo_plug_001.energy"] == 0.059


def test_parse_sensor_data_from_dps() -> None:
    """Sensor parser should map temp/humidity from DPS keys 1 and 2."""
    getter = TuyaGetter(DataStore(), devices=[], interval=10.0)
    device = TuyaDeviceConfig(
        device_id="demo_sensor_001",
        local_key="k",
        device_type="sensor",
        ip_address=None,
    )
    status = {"dps": {"1": 199, "2": 47}}

    parsed = getter._parse_sensor_data(device, status)

    assert parsed["tuya.sensors.demo_sensor_001.temperature"] == 67.82
    assert parsed["tuya.sensors.demo_sensor_001.humidity"] == 47.0


def test_get_or_create_handle_bounds_socket_timeout_and_retries() -> None:
    """Device handles must use short, bounded socket timeouts/retries.

    An unreachable Tuya device must fail fast rather than block the polling
    worker thread for minutes (tinytuya's defaults are a 5s timeout with 5
    retries and a 5s inter-retry delay, i.e. up to ~50s per protocol version
    attempted). A stuck asyncio.to_thread call cannot be cancelled once
    started and blocks daemon shutdown/host reboot, so these must stay tight.
    """
    getter = TuyaGetter(DataStore(), devices=[], interval=10.0)
    device = TuyaDeviceConfig(
        device_id="demo_plug_002",
        local_key="0123456789abcdef",
        device_type="plug",
        ip_address="192.0.2.1",
    )

    handle = getter._get_or_create_handle(device)

    assert handle.connection_timeout == _TUYA_SOCKET_TIMEOUT_SECONDS
    assert handle.socketRetryLimit == _TUYA_SOCKET_RETRY_LIMIT
    assert handle.socketRetryDelay == _TUYA_SOCKET_RETRY_DELAY_SECONDS
    # Reusing the same device must not create a second handle or re-apply
    # different settings.
    assert getter._get_or_create_handle(device) is handle
