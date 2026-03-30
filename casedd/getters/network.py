"""Network throughput getter.

Computes per-interval byte rates for one or more network interfaces by
diffing successive ``psutil.net_io_counters`` snapshots.

When ``interfaces`` is specified only those physical NICs are summed,
excluding Docker bridges, veth pairs, loopback, and other virtual devices.
Leaving ``interfaces`` empty falls back to the psutil aggregate (all
interfaces), which is the legacy behaviour.

Store keys written:
    - ``net.recv_mbps`` (float) — receive rate in Mb/s
    - ``net.sent_mbps`` (float) — transmit rate in Mb/s
    - ``net.bytes_recv_rate`` (float) — receive rate in MB/s
    - ``net.bytes_sent_rate`` (float) — transmit rate in MB/s
    - ``net.bytes_recv_total`` (float) — cumulative received MB
    - ``net.bytes_sent_total`` (float) — cumulative sent MB
"""

import asyncio
import logging
import time

import psutil

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)
_MB = 1024 * 1024
_MEGABIT = 1_000_000
_VIRTUAL_INTERFACE_PREFIXES = (
    "lo",
    "docker",
    "br-",
    "veth",
    "virbr",
    "vboxnet",
    "vmnet",
    "tap",
    "tun",
    "tailscale",
    "zt",
    "wg",
)


def _default_interface_names() -> list[str]:
    """Choose likely host uplink interfaces when none are configured."""
    selected: list[str] = []
    for name, stats in psutil.net_if_stats().items():
        lowered = name.lower()
        if not stats.isup:
            continue
        if any(lowered.startswith(prefix) for prefix in _VIRTUAL_INTERFACE_PREFIXES):
            continue
        selected.append(name)
    return sorted(selected)


class NetworkGetter(BaseGetter):
    """Getter for network I/O rates.

    Rates are computed as the delta of psutil counters divided by the elapsed
    time since the previous sample, accurately tracking burst traffic.

    Args:
        store: Shared data store instance.
        interval: Poll interval in seconds (default: 2.0).
        interfaces: Explicit NIC names to sum (e.g. ``["enp8s0"]``). Docker
            bridges, veth pairs, and other virtual interfaces are automatically
            excluded. Empty list aggregates all interfaces (legacy mode).
    """

    def __init__(
        self,
        store: DataStore,
        interval: float = 2.0,
        interfaces: list[str] | None = None,
    ) -> None:
        """Initialise the network getter and take the first counter snapshot.

        Args:
            store: The shared :class:`~casedd.data_store.DataStore`.
            interval: Seconds between each poll (default: 2.0).
            interfaces: Optional list of NIC names to monitor. Empty / None
                falls back to the psutil aggregate across all interfaces.
        """
        super().__init__(store, interval)
        self._interfaces: list[str] = (
            list(interfaces) if interfaces else _default_interface_names()
        )
        if self._interfaces:
            _log.info(
                "Network getter monitoring interfaces: %s",
                ", ".join(self._interfaces),
            )
        else:
            _log.info(
                "Network getter found no eligible physical interfaces; monitoring all "
                "interfaces (aggregate mode)."
            )
        recv, sent = self._read_counters()
        self._last_recv: int = recv
        self._last_sent: int = sent
        self._last_time: float = time.monotonic()

    def _read_counters(self) -> tuple[int, int]:
        """Return (bytes_recv, bytes_sent) summed over the configured interfaces.

        Returns:
            Tuple of (recv_bytes, sent_bytes) for the monitored interfaces.
        """
        if self._interfaces:
            pernic = psutil.net_io_counters(pernic=True)
            recv = sum(
                pernic[iface].bytes_recv for iface in self._interfaces if iface in pernic
            )
            sent = sum(
                pernic[iface].bytes_sent for iface in self._interfaces if iface in pernic
            )
            return recv, sent
        c = psutil.net_io_counters()
        return c.bytes_recv, c.bytes_sent

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
        recv, sent = self._read_counters()

        elapsed = max(now - self._last_time, 0.001)

        recv_delta = recv - self._last_recv
        sent_delta = sent - self._last_sent

        recv_rate_mb = recv_delta / elapsed / _MB
        sent_rate_mb = sent_delta / elapsed / _MB
        recv_rate_mbit = recv_delta * 8.0 / elapsed / _MEGABIT
        sent_rate_mbit = sent_delta * 8.0 / elapsed / _MEGABIT

        self._last_recv = recv
        self._last_sent = sent
        self._last_time = now

        return {
            "net.recv_mbps": round(max(recv_rate_mbit, 0.0), 3),
            "net.sent_mbps": round(max(sent_rate_mbit, 0.0), 3),
            "net.bytes_recv_rate": round(max(recv_rate_mb, 0.0), 3),
            "net.bytes_sent_rate": round(max(sent_rate_mb, 0.0), 3),
            "net.bytes_recv_total": round(recv / _MB, 1),
            "net.bytes_sent_total": round(sent / _MB, 1),
        }
