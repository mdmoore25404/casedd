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
import logging
from typing import Any

import tinytuya  # type: ignore[import-untyped]

from casedd.config import TuyaDeviceConfig
from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)


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
                result.update(data)
                self._last_error[device.device_id] = ""
            except Exception as exc:
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
        status: dict[str, Any],
    ) -> dict[str, StoreValue]:
        """Extract temperature and humidity from sensor status.

        Args:
            device: TuyaDeviceConfig instance.
            status: Raw status dict from tinytuya.

        Returns:
            Dict of ``tuya.sensors.<id>.temperature/humidity`` keys.
        """
        result: dict[str, StoreValue] = {}
        dps = status.get("dps", {})

        # Common DPS assignments (may vary by manufacturer)
        # DPS 1: temperature (usually in °C, scaled by 10)
        # DPS 2: humidity (%)
        if "1" in dps:
            temp_raw = dps["1"]
            temp = float(temp_raw) / 10.0 if isinstance(temp_raw, (int, float)) else 0.0
            result[f"tuya.sensors.{device.device_id}.temperature"] = temp

        if "2" in dps:
            humidity_raw = dps["2"]
            humidity = (
                float(humidity_raw)
                if isinstance(humidity_raw, (int, float))
                else 0.0
            )
            result[f"tuya.sensors.{device.device_id}.humidity"] = humidity

        return result

    def _parse_plug_data(
        self,
        device: TuyaDeviceConfig,
        status: dict[str, Any],
    ) -> dict[str, StoreValue]:
        """Extract power, current, voltage from smart plug status.

        Args:
            device: TuyaDeviceConfig instance.
            status: Raw status dict from tinytuya.

        Returns:
            Dict of ``tuya.plugs.<id>.power/current/voltage/energy`` keys.
        """
        result: dict[str, StoreValue] = {}
        dps = status.get("dps", {})

        # Common DPS assignments (documented or empirically observed)
        # DPS 1: Power state (bool) — not used.
        # DPS 6: Current in mA
        # DPS 19: Power in watts
        # DPS 20: Voltage in volts
        # DPS 26: Total energy in kWh (scaled by 100)
        # DPS 104: Power state (bool, newer protocol)
        # NOTE: These vary by manufacturer; update as needed for specific plugs.

        if "19" in dps:
            power_raw = dps["19"]
            power = float(power_raw) / 10.0 if isinstance(power_raw, (int, float)) else 0.0
            result[f"tuya.plugs.{device.device_id}.power"] = power

        if "6" in dps:
            current_raw = dps["6"]
            current = (
                float(current_raw) if isinstance(current_raw, (int, float)) else 0.0
            )
            result[f"tuya.plugs.{device.device_id}.current"] = current

        if "20" in dps:
            voltage_raw = dps["20"]
            voltage = float(voltage_raw) / 10.0 if isinstance(voltage_raw, (int, float)) else 0.0
            result[f"tuya.plugs.{device.device_id}.voltage"] = voltage

        if "26" in dps:
            energy_raw = dps["26"]
            energy = float(energy_raw) / 100.0 if isinstance(energy_raw, (int, float)) else 0.0
            result[f"tuya.plugs.{device.device_id}.energy"] = energy

        return result
