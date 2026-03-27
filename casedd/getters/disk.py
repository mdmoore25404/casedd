"""Disk usage getter.

Polls disk usage for a configurable mount point via ``psutil.disk_usage``
and publishes results under the ``disk.*`` namespace.

Store keys written:
    - ``disk.percent`` (float) -- disk usage 0-100
    - ``disk.used_gb`` (float) — space used in GB
    - ``disk.total_gb`` (float) — total space in GB
    - ``disk.free_gb`` (float) — free space in GB
"""

import asyncio

import psutil

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_GiB = 1024 ** 3


class DiskGetter(BaseGetter):
    """Getter for disk usage metrics.

    Args:
        store: Shared data store instance.
        mount: Mount point to monitor (default: ``"/"``).
        interval: Poll interval in seconds (default: 10.0).
    """

    def __init__(
        self,
        store: DataStore,
        mount: str = "/",
        interval: float = 10.0,
    ) -> None:
        """Initialise the disk getter.

        Args:
            store: The shared :class:`~casedd.data_store.DataStore`.
            mount: Filesystem mount point to query (default: ``"/"``).
            interval: Seconds between each poll (default: 10.0).
        """
        super().__init__(store, interval)
        self._mount = mount

    async def fetch(self) -> dict[str, StoreValue]:
        """Sample disk metrics.

        Returns:
            Dict with ``disk.*`` keys.
        """
        mount = self._mount  # capture for thread
        return await asyncio.to_thread(self._sample, mount)

    @staticmethod
    def _sample(mount: str) -> dict[str, StoreValue]:
        """Blocking disk sample.

        Args:
            mount: Mount point path string.

        Returns:
            Dict of store updates.
        """
        du = psutil.disk_usage(mount)
        return {
            "disk.percent": float(du.percent),
            "disk.used_gb": round(du.used / _GiB, 2),
            "disk.total_gb": round(du.total / _GiB, 2),
            "disk.free_gb": round(du.free / _GiB, 2),
        }
