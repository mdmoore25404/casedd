"""Memory (RAM) data getter.

Polls physical RAM usage via ``psutil.virtual_memory`` and publishes results
under the ``memory.*`` namespace in the data store.

Store keys written:
    - ``memory.percent`` (float) -- RAM usage 0-100
    - ``memory.used_gb`` (float) — RAM used in GB
    - ``memory.total_gb`` (float) — RAM total in GB
    - ``memory.available_gb`` (float) — RAM available in GB
"""

import asyncio

import psutil

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_GiB = 1024 ** 3


class MemoryGetter(BaseGetter):
    """Getter for physical RAM usage.

    Args:
        store: Shared data store instance.
        interval: Poll interval in seconds (default: 2.0).
    """

    def __init__(self, store: DataStore, interval: float = 2.0) -> None:
        """Initialise the memory getter.

        Args:
            store: The shared :class:`~casedd.data_store.DataStore`.
            interval: Seconds between each poll (default: 2.0).
        """
        super().__init__(store, interval)

    async def fetch(self) -> dict[str, StoreValue]:
        """Sample RAM metrics.

        Returns:
            Dict with ``memory.*`` keys.
        """
        return await asyncio.to_thread(self._sample)

    @staticmethod
    def _sample() -> dict[str, StoreValue]:
        """Blocking RAM sample.

        Returns:
            Dict of store updates.
        """
        vm = psutil.virtual_memory()
        return {
            "memory.percent": float(vm.percent),
            "memory.used_gb": round(vm.used / _GiB, 2),
            "memory.total_gb": round(vm.total / _GiB, 2),
            "memory.available_gb": round(vm.available / _GiB, 2),
        }
