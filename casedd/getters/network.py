"""Network throughput getter.

Computes per-interval byte rates for the primary network interface by
diffing successive ``psutil.net_io_counters`` snapshots.

Store keys written:
    - ``net.bytes_recv_rate`` (float) — receive rate in MB/s
    - ``net.bytes_sent_rate`` (float) — send rate in MB/s
    - ``net.bytes_recv_total`` (float) — cumulative received MB
    - ``net.bytes_sent_total`` (float) — cumulative sent MB
"""

import asyncio
import time

import psutil

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_MB = 1024 * 1024


class NetworkGetter(BaseGetter):
    """Getter for network I/O rates.

    Rates are computed as the delta of psutil counters divided by the elapsed
    time since the previous sample, so accurately tracks burst traffic.

    Args:
        store: Shared data store instance.
        interval: Poll interval in seconds (default: 2.0).
    """

    def __init__(self, store: DataStore, interval: float = 2.0) -> None:
        """Initialise the network getter and take the first counter snapshot.

        Args:
            store: The shared :class:`~casedd.data_store.DataStore`.
            interval: Seconds between each poll (default: 2.0).
        """
        super().__init__(store, interval)
        counters = psutil.net_io_counters()
        self._last_recv: int = counters.bytes_recv
        self._last_sent: int = counters.bytes_sent
        self._last_time: float = time.monotonic()

    async def fetch(self) -> dict[str, StoreValue]:
        """Sample network I/O rates.

        Returns:
            Dict with ``net.*`` keys.
        """
        return await asyncio.to_thread(self._sample)

    def _sample(self) -> dict[str, StoreValue]:
        """Blocking network sample — computes delta rates.

        Returns:
            Dict of store updates.
        """
        now = time.monotonic()
        counters = psutil.net_io_counters()

        elapsed = now - self._last_time
        # Avoid division by zero on very first poll or clock skip
        elapsed = max(elapsed, 0.001)

        recv_rate = (counters.bytes_recv - self._last_recv) / elapsed / _MB
        sent_rate = (counters.bytes_sent - self._last_sent) / elapsed / _MB

        self._last_recv = counters.bytes_recv
        self._last_sent = counters.bytes_sent
        self._last_time = now

        return {
            "net.bytes_recv_rate": round(max(recv_rate, 0.0), 3),
            "net.bytes_sent_rate": round(max(sent_rate, 0.0), 3),
            "net.bytes_recv_total": round(counters.bytes_recv / _MB, 1),
            "net.bytes_sent_total": round(counters.bytes_sent / _MB, 1),
        }
