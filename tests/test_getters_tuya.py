"""Tests for Tuya getter parsing.

Covers plug DPS mappings and temperature/humidity sensor mappings.
"""

from __future__ import annotations

from casedd.config import TuyaDeviceConfig
from casedd.data_store import DataStore
from casedd.getters.tuya import TuyaGetter


def test_parse_plug_data_uses_cur_mappings() -> None:
    """Plug parser should map current/power/voltage/energy from observed DPS keys."""
    getter = TuyaGetter(DataStore(), devices=[], interval=10.0)
    device = TuyaDeviceConfig(
        device_id="eb0a215398dcb86f25rlfa",
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

    assert parsed["tuya.plugs.eb0a215398dcb86f25rlfa.current"] == 915.0
    assert parsed["tuya.plugs.eb0a215398dcb86f25rlfa.power"] == 111.2
    assert parsed["tuya.plugs.eb0a215398dcb86f25rlfa.voltage"] == 121.0
    assert parsed["tuya.plugs.eb0a215398dcb86f25rlfa.energy"] == 0.059


def test_parse_sensor_data_from_dps() -> None:
    """Sensor parser should map temp/humidity from DPS keys 1 and 2."""
    getter = TuyaGetter(DataStore(), devices=[], interval=10.0)
    device = TuyaDeviceConfig(
        device_id="ebdfd261260ea1d162tk7o",
        local_key="k",
        device_type="sensor",
        ip_address=None,
    )
    status = {"dps": {"1": 199, "2": 47}}

    parsed = getter._parse_sensor_data(device, status)

    assert parsed["tuya.sensors.ebdfd261260ea1d162tk7o.temperature"] == 19.9
    assert parsed["tuya.sensors.ebdfd261260ea1d162tk7o.humidity"] == 47.0
