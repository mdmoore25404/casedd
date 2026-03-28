"""Network listening ports getter (netstat-style).

Publishes a table of currently listening TCP/UDP sockets via ``psutil``.
TCP sockets in ``LISTEN`` state and all bound UDP sockets are included.
Rows are deduplicated by ``(proto, port)`` and sorted by port number.

Store keys written:
    - ``netports.port_count`` (float) -- number of distinct listening entries
    - ``netports.rows``       (str)   -- newline-delimited pipe-separated rows
      Each row format: ``PROTO|PORT|ADDR|PID|NAME``
"""

from __future__ import annotations

import asyncio
import logging
import socket as _socket

import psutil

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)


class NetPortsGetter(BaseGetter):
    """Getter producing a netstat-like table of active listening sockets."""

    def __init__(self, store: DataStore, interval: float = 5.0) -> None:
        """Initialise the network ports getter.

        Args:
            store: Shared data store instance.
            interval: Poll interval in seconds (default: 5.0).
        """
        super().__init__(store, interval)

    async def fetch(self) -> dict[str, StoreValue]:
        """Collect one snapshot of listening sockets.

        Returns:
            Dict of ``netports.*`` store updates.
        """
        return await asyncio.to_thread(self._sample)

    def _sample(self) -> dict[str, StoreValue]:
        """Enumerate listening sockets and build structured row data.

        Returns:
            Store update dict with ``netports.port_count`` and
            ``netports.rows``.
        """
        try:
            connections = psutil.net_connections(kind="inet")
        except psutil.AccessDenied:
            _log.warning(
                "Access denied enumerating network connections; "
                "run as root for complete port visibility"
            )
            connections = []

        pid_name_cache: dict[int, str] = {}
        seen: set[tuple[str, int]] = set()
        # (port, proto, addr, pid_str, name) for sorting by port then proto
        rows: list[tuple[int, str, str, str, str]] = []

        for conn in connections:
            laddr = conn.laddr
            if not laddr:
                continue

            is_tcp = conn.type == _socket.SOCK_STREAM
            proto = "TCP" if is_tcp else "UDP"

            # TCP: only LISTEN state; UDP: all bound sockets
            if is_tcp and conn.status != "LISTEN":
                continue

            port = laddr.port
            key = (proto, port)
            if key in seen:
                continue
            seen.add(key)

            raw_addr = laddr.ip or "*"
            # Collapse wildcard bind addresses to "*" for compact display.
            addr = "*" if raw_addr in ("0.0.0.0", "::") else raw_addr  # noqa: S104

            pid = conn.pid
            name = "-"
            if pid is not None:
                if pid not in pid_name_cache:
                    try:
                        pid_name_cache[pid] = psutil.Process(pid).name()
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        pid_name_cache[pid] = "-"
                name = pid_name_cache[pid]

            pid_str = str(pid) if pid is not None else "-"
            rows.append((port, proto, addr, pid_str, name))

        rows.sort()  # primary key: port number, secondary: proto

        rendered = [
            f"{proto}|{port}|{addr}|{pid_str}|{name}"
            for port, proto, addr, pid_str, name in rows
        ]
        return {
            "netports.port_count": float(len(rows)),
            "netports.rows": "\n".join(rendered),
        }
