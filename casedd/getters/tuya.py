"""Tuya smart device getter.

Polls Tuya smart home devices (temperature/humidity sensors, smart plugs with
power monitoring) via local network protocol. READ-ONLY — no state commands.

Store keys written:
    - ``tuya.sensors.<device_id>.temperature`` (float, °C)
    - ``tuya.sensors.<device_id>.humidity`` (float, %)
    - ``tuya.plugs.<device_id>.power`` (float, watts)
    - ``tuya.plugs.<device_id>.current`` (float, mA)
    - ``tuya.plugs.<device_id>.voltage`` (float, V)
    - ``tuya.plugs.<device_id>.energy`` (float, kWh)
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
import logging

import tinytuya  # type: ignore[import-untyped]

from casedd.config import TuyaDeviceConfig
from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TuyaCloudSettings:
    """Optional Tuya cloud API settings for read-only fallback polling."""

    enabled: bool = False
    region: str = ""
    api_key: str | None = None
    api_secret: str | None = None
    api_device_id: str | None = None


def _coerce_number(value: object) -> float | None:
    """Convert a raw Tuya value into a float when possible.

    Args:
        value: Raw value from DPS/status payload.

    Returns:
        Parsed float, or ``None`` when conversion is not possible.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _status_code_values(status: Mapping[str, object]) -> dict[str, float]:
    """Build a ``code -> numeric value`` map from cloud-style status arrays.

    Args:
        status: Raw device status payload.

    Returns:
        Dictionary of status code names mapped to numeric values.
    """
    result: dict[str, float] = {}
    raw_status = status.get("status")
    if not isinstance(raw_status, list):
        raw_status = status.get("result")
    if not isinstance(raw_status, list):
        return result
    for item in raw_status:
        if not isinstance(item, Mapping):
            continue
        code = item.get("code")
        if not isinstance(code, str):
            continue
        number = _coerce_number(item.get("value"))
        if number is None:
            continue
        result[code] = number
    return result


def _dps_values(status: Mapping[str, object]) -> dict[str, float]:
    """Build a numeric DPS map from local-protocol payloads.

    Args:
        status: Raw device status payload.

    Returns:
        Dictionary of DPS string keys mapped to numeric values.
    """
    result: dict[str, float] = {}
    raw_dps = status.get("dps")
    if not isinstance(raw_dps, Mapping):
        return result
    for key, value in raw_dps.items():
        if not isinstance(key, str):
            continue
        number = _coerce_number(value)
        if number is None:
            continue
        result[key] = number
    return result


def _first_present(values: Mapping[str, float], candidates: tuple[str, ...]) -> float | None:
    """Return the first candidate key present in a values map.

    Args:
        values: Candidate numeric values map.
        candidates: Keys to check in order.

    Returns:
        Matching value when found, otherwise ``None``.
    """
    for key in candidates:
        if key in values:
            return values[key]
    return None


class TuyaGetter(BaseGetter):
    """Fetch data from Tuya smart home devices.

    Connects to devices via local network (not cloud), reading temperature/
    humidity sensors and smart plug power data. Handles per-device connection
    pooling and graceful degradation when devices are offline.

    Args:
        store: Shared data store instance.
        devices: List of TuyaDeviceConfig instances to poll.
        interval: Poll interval in seconds (default 10.0).
    """

    def __init__(
        self,
        store: DataStore,
        devices: list[TuyaDeviceConfig],
        interval: float = 10.0,
        cloud_settings: TuyaCloudSettings | None = None,
    ) -> None:
        """Initialize the Tuya getter.

        Args:
            store: Shared data store.
            devices: List of TuyaDeviceConfig configs to monitor.
            interval: Polling interval in seconds.
        """
        super().__init__(store, interval)
        self._devices = devices
        self._device_handles: dict[str, tinytuya.Device] = {}
        self._last_error: dict[str, str] = {}
        self._cloud: tinytuya.Cloud | None = None
        settings = cloud_settings or TuyaCloudSettings()
        if (
            settings.enabled
            and settings.region
            and settings.api_key
            and settings.api_secret
            and settings.api_device_id
        ):
            self._cloud = tinytuya.Cloud(
                apiRegion=settings.region,
                apiKey=settings.api_key,
                apiSecret=settings.api_secret,
                apiDeviceID=settings.api_device_id,
            )

    async def fetch(self) -> dict[str, StoreValue]:
        """Poll all configured Tuya devices and return aggregated data.

        Connection errors are logged once per device; subsequent calls skip
        that device until it recovers.

        Returns:
            Dict of ``tuya.sensors.<id>.*`` and ``tuya.plugs.<id>.*`` keys.
        """
        result: dict[str, StoreValue] = {}

        for device in self._devices:
            try:
                data = await asyncio.to_thread(
                    self._poll_device,
                    device,
                )
                if not data and self._cloud is not None:
                    data = await asyncio.to_thread(self._poll_device_cloud, device)
                result.update(data)
                self._last_error[device.device_id] = ""
            except Exception as exc:
                if self._cloud is not None:
                    try:
                        cloud_data = await asyncio.to_thread(self._poll_device_cloud, device)
                        result.update(cloud_data)
                        self._last_error[device.device_id] = ""
                        continue
                    except Exception:
                        _log.debug(
                            "Cloud fallback failed for Tuya device %s",
                            device.device_id,
                            exc_info=True,
                        )
                error_msg = str(exc)
                # Log only on first failure or if error changes
                if self._last_error.get(device.device_id) != error_msg:
                    _log.warning(
                        "Tuya device %s (%s) offline: %s",
                        device.device_id,
                        device.ip_address or "discover",
                        error_msg,
                    )
                    self._last_error[device.device_id] = error_msg

        return result

    def _poll_device_cloud(self, device: TuyaDeviceConfig) -> dict[str, StoreValue]:
        """Poll a single Tuya device via cloud API fallback.

        Args:
            device: TuyaDeviceConfig instance to poll.

        Returns:
            Dict of updated store keys for this device.

        Raises:
            RuntimeError: If cloud polling is unavailable or fails.
        """
        if self._cloud is None:
            msg = "Cloud polling is not configured"
            raise RuntimeError(msg)
        status = self._cloud.getstatus(device.device_id)
        if not isinstance(status, Mapping):
            msg = "Invalid cloud status response"
            raise RuntimeError(msg)
        if status.get("success") is False:
            msg = f"Cloud status fetch failed for {device.device_id}"
            raise RuntimeError(msg)

        if device.device_type == "sensor":
            return self._parse_sensor_data(device, status)
        if device.device_type == "plug":
            return self._parse_plug_data(device, status)
        return {}

    def _poll_device(self, device: TuyaDeviceConfig) -> dict[str, StoreValue]:
        """Poll a single Tuya device.

        Args:
            device: TuyaDeviceConfig instance to poll.

        Returns:
            Dict of updated store keys for this device.

        Raises:
            RuntimeError: If device connection fails.
        """
        handle = self._get_or_create_handle(device)
        handle.set_version(3.3)  # Protocol version for local polling

        # Fetch device status — structure varies by device type
        status = handle.status()
        if not status:
            msg = "No status received from device"
            raise RuntimeError(msg)

        result: dict[str, StoreValue] = {}

        if device.device_type == "sensor":
            # Temperature/humidity sensor — dps keys typically 1=temp, 2=humidity
            result.update(self._parse_sensor_data(device, status))
        elif device.device_type == "plug":
            # Smart plug with power monitoring — dps keys: 1=power, 6=current, 20=voltage
            result.update(self._parse_plug_data(device, status))

        return result

    def _get_or_create_handle(self, device: TuyaDeviceConfig) -> tinytuya.Device:
        """Reuse or create a device handle for polling.

        Args:
            device: TuyaDeviceConfig instance.

        Returns:
            tinytuya.Device handle for local polling.
        """
        if device.device_id in self._device_handles:
            return self._device_handles[device.device_id]

        handle = tinytuya.Device(
            dev_id=device.device_id,
            address=device.ip_address or "",
            local_key=device.local_key,
        )
        self._device_handles[device.device_id] = handle
        return handle

    def _parse_sensor_data(
        self,
        device: TuyaDeviceConfig,
        status: Mapping[str, object],
    ) -> dict[str, StoreValue]:
        """Extract temperature and humidity from sensor status.

        Args:
            device: TuyaDeviceConfig instance.
            status: Raw status dict from tinytuya.

        Returns:
            Dict of ``tuya.sensors.<id>.temperature/humidity`` keys.
        """
        result: dict[str, StoreValue] = {}
        dps = _dps_values(status)
        codes = _status_code_values(status)

        # Common DPS assignments (may vary by manufacturer)
        # DPS 1: temperature (usually in °C, scaled by 10)
        # DPS 2: humidity (%)
        temp_raw = _first_present(dps, ("1",))
        if temp_raw is None:
            temp_raw = _first_present(codes, ("va_temperature",))
        if temp_raw is not None:
            temp = float(temp_raw) / 10.0
            result[f"tuya.sensors.{device.device_id}.temperature"] = temp

        humidity_raw = _first_present(dps, ("2",))
        if humidity_raw is None:
            humidity_raw = _first_present(codes, ("humidity_value",))
        if humidity_raw is not None:
            humidity = float(humidity_raw)
            result[f"tuya.sensors.{device.device_id}.humidity"] = humidity

        return result

    def _parse_plug_data(
        self,
        device: TuyaDeviceConfig,
        status: Mapping[str, object],
    ) -> dict[str, StoreValue]:
        """Extract power, current, voltage from smart plug status.

        Args:
            device: TuyaDeviceConfig instance.
            status: Raw status dict from tinytuya.

        Returns:
            Dict of ``tuya.plugs.<id>.power/current/voltage/energy`` keys.
        """
        result: dict[str, StoreValue] = {}
        dps = _dps_values(status)
        codes = _status_code_values(status)

        # Common DPS assignments (documented or empirically observed)
        # DPS 1: Power state (bool) — not used.
        # DPS 6/18: Current in mA
        # DPS 19: Power in watts (scale 1)
        # DPS 20: Voltage in volts (scale 1)
        # DPS 17: Total energy in kWh (scale 3)
        # DPS 104: Power state (bool, newer protocol)
        # NOTE: These vary by manufacturer; update as needed for specific plugs.

        power_raw = _first_present(dps, ("19",))
        if power_raw is None:
            power_raw = _first_present(codes, ("cur_power",))
        if power_raw is not None:
            power = float(power_raw) / 10.0
            result[f"tuya.plugs.{device.device_id}.power"] = power

        current_raw = _first_present(dps, ("18", "6"))
        if current_raw is None:
            current_raw = _first_present(codes, ("cur_current",))
        if current_raw is not None:
            current = float(current_raw)
            result[f"tuya.plugs.{device.device_id}.current"] = current

        voltage_raw = _first_present(dps, ("20",))
        if voltage_raw is None:
            voltage_raw = _first_present(codes, ("cur_voltage",))
        if voltage_raw is not None:
            voltage = float(voltage_raw) / 10.0
            result[f"tuya.plugs.{device.device_id}.voltage"] = voltage

        energy_raw = _first_present(dps, ("17",))
        if energy_raw is None:
            energy_raw = _first_present(codes, ("add_ele",))
        if energy_raw is not None:
            energy = float(energy_raw) / 1000.0
            result[f"tuya.plugs.{device.device_id}.energy"] = energy

        return result
