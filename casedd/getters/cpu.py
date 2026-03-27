"""CPU data getter.

Polls CPU usage, temperature, and fan RPM via ``psutil`` and publishes them
to the data store under the ``cpu.*`` namespace.

Store keys written:
    - ``cpu.percent`` (float) -- overall CPU usage 0-100
    - ``cpu.temperature`` (float) — CPU package temperature in °C (if available)
    - ``cpu.fan_rpm`` (float) — first CPU fan RPM (if available; 0.0 if not)
"""

import asyncio
import logging

import psutil

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)


class CpuGetter(BaseGetter):
    """Getter for CPU utilisation, temperature, and fan speed.

    Uses ``psutil.cpu_percent`` (non-blocking interval=0 variant called on a
    thread to avoid stalling the event loop), ``psutil.sensors_temperatures``,
    and ``psutil.sensors_fans``.

    Args:
        store: Shared data store instance.
        interval: Poll interval in seconds (default: 2.0).
    """

    def __init__(self, store: DataStore, interval: float = 2.0) -> None:
        """Initialise the CPU getter.

        Args:
            store: The shared :class:`~casedd.data_store.DataStore`.
            interval: Seconds between each poll (default: 2.0).
        """
        super().__init__(store, interval)
        # Prime psutil so the first call to cpu_percent returns a valid number
        psutil.cpu_percent(interval=None)

    async def fetch(self) -> dict[str, StoreValue]:
        """Sample CPU metrics.

        Runs the blocking ``psutil`` calls in a thread pool to avoid blocking
        the event loop.

        Returns:
            Dict with ``cpu.percent``, ``cpu.temperature``, ``cpu.fan_rpm``.
        """
        return await asyncio.to_thread(self._sample)

    def _sample(self) -> dict[str, StoreValue]:
        """Blocking CPU sample — called via ``asyncio.to_thread``.

        Returns:
            Dict of store updates.
        """
        result: dict[str, StoreValue] = {}

        result["cpu.percent"] = psutil.cpu_percent(interval=None)

        # Temperature — try common sensor key names
        temp = self._read_temperature()
        if temp is not None:
            result["cpu.temperature"] = temp

        # Fan RPM — first entry from any sensor
        fan = self._read_fan_rpm()
        result["cpu.fan_rpm"] = fan

        return result

    @staticmethod
    def _read_temperature() -> float | None:
        """Read CPU package temperature from psutil sensors.

        Returns:
            Temperature in °C, or ``None`` if unavailable.
        """
        if not hasattr(psutil, "sensors_temperatures"):
            return None
        temps = psutil.sensors_temperatures()
        # Try known sensor keys in priority order
        for key in ("coretemp", "k10temp", "acpitz", "cpu_thermal"):
            entries = temps.get(key)
            if entries:
                # Prefer the 'Package id 0' / 'Tdie' / 'temp1' entry
                for entry in entries:
                    if "package" in entry.label.lower() or "tdie" in entry.label.lower():
                        return float(entry.current)
                # Fall back to the first entry
                return float(entries[0].current)
        return None

    @staticmethod
    def _read_fan_rpm() -> float:
        """Read the first CPU fan RPM from psutil sensors.

        Returns:
            RPM as float, or 0.0 if unavailable.
        """
        if not hasattr(psutil, "sensors_fans"):
            return 0.0
        fans = psutil.sensors_fans()
        for entries in fans.values():
            if entries:
                return float(entries[0].current)
        return 0.0
