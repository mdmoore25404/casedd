"""System info getter.

Publishes static and slowly-changing system information under the
``system.*`` namespace.

Store keys written:
    - ``system.hostname`` (str) — machine hostname
    - ``system.uptime`` (str) — human-readable uptime, e.g. ``"3d 4h 12m"``
    - ``system.load_1`` (float) — 1-minute load average
    - ``system.load_5`` (float) — 5-minute load average
    - ``system.load_15`` (float) — 15-minute load average
    - ``system.boot_time`` (float) — Unix timestamp of last boot
"""

import asyncio
import socket
import time

import psutil

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter


def _format_uptime(seconds: float) -> str:
    """Convert an uptime in seconds to a compact human-readable string.

    Args:
        seconds: Uptime in seconds.

    Returns:
        Formatted string, e.g. ``"3d 4h 12m"`` or ``"42m"`` for short uptimes.
    """
    secs = int(seconds)
    days, remainder = divmod(secs, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


class SystemGetter(BaseGetter):
    """Getter for general system information (hostname, uptime, load average).

    Args:
        store: Shared data store instance.
        interval: Poll interval in seconds (default: 10.0).
    """

    def __init__(self, store: DataStore, interval: float = 10.0) -> None:
        """Initialise the system getter.

        Args:
            store: The shared :class:`~casedd.data_store.DataStore`.
            interval: Seconds between each poll (default: 10.0).
        """
        super().__init__(store, interval)

    async def fetch(self) -> dict[str, StoreValue]:
        """Sample system information.

        Returns:
            Dict with ``system.*`` keys.
        """
        return await asyncio.to_thread(self._sample)

    @staticmethod
    def _sample() -> dict[str, StoreValue]:
        """Blocking system info sample.

        Returns:
            Dict of store updates.
        """
        boot_time = psutil.boot_time()
        uptime_secs = time.time() - boot_time
        load_1, load_5, load_15 = psutil.getloadavg()

        return {
            "system.hostname": socket.gethostname(),
            "system.uptime": _format_uptime(uptime_secs),
            "system.load_1": round(load_1, 2),
            "system.load_5": round(load_5, 2),
            "system.load_15": round(load_15, 2),
            "system.boot_time": boot_time,
        }
