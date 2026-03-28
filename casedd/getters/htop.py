"""Htop-style process table getter.

Publishes top processes by CPU utilization using ``psutil``.
The payload is normalized under the ``htop.*`` namespace so templates can
render a compact htop-like process list.

Store keys written:
    - ``htop.process_count`` (float)
    - ``htop.rows`` (str) -- newline-delimited process rows
    - ``htop.summary`` (str)
    - ``htop.top_name`` (str)
    - ``htop.top_cpu`` (float)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging

import psutil

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ProcRow:
    """One process row for htop-style display."""

    pid: int
    cpu: float
    mem: float
    name: str


class HtopGetter(BaseGetter):
    """Getter producing htop-like process rows sorted by CPU usage."""

    def __init__(self, store: DataStore, interval: float = 2.0, max_rows: int = 12) -> None:
        """Initialize process table getter.

        Args:
            store: Shared data store instance.
            interval: Poll interval in seconds.
            max_rows: Maximum rows to publish.
        """
        super().__init__(store, interval)
        self._max_rows = max_rows

    async def fetch(self) -> dict[str, StoreValue]:
        """Collect one htop-style process snapshot."""
        return await asyncio.to_thread(self._sample)

    def _sample(self) -> dict[str, StoreValue]:
        """Blocking process snapshot implementation."""
        rows: list[_ProcRow] = []

        for proc in psutil.process_iter(attrs=["pid", "name", "memory_percent"]):
            try:
                pid = int(proc.info.get("pid", 0))
                name_raw = proc.info.get("name")
                name = str(name_raw) if isinstance(name_raw, str) and name_raw else "unknown"
                mem_raw = proc.info.get("memory_percent")
                mem = float(mem_raw) if isinstance(mem_raw, int | float) else 0.0
                cpu = float(proc.cpu_percent(interval=None))
                rows.append(_ProcRow(pid=pid, cpu=max(0.0, cpu), mem=max(0.0, mem), name=name))
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except Exception:
                _log.debug("failed to inspect process", exc_info=True)

        rows.sort(key=lambda item: (item.cpu, item.mem), reverse=True)
        top_rows = rows[: self._max_rows]

        if not top_rows:
            return {
                "htop.process_count": 0.0,
                "htop.rows": "No process data",
                "htop.summary": "No process data",
                "htop.top_name": "",
                "htop.top_cpu": 0.0,
            }

        # Pipe-delimited structured rows: PID|CPU|MEM|NAME
        # The renderer controls sort order and column formatting at draw time.
        rendered_rows = [
            f"{row.pid}|{row.cpu:.2f}|{row.mem:.2f}|{row.name}"
            for row in top_rows
        ]
        top = top_rows[0]
        return {
            "htop.process_count": float(len(rows)),
            "htop.rows": "\n".join(rendered_rows),
            "htop.summary": f"Top CPU: {top.name} {top.cpu:.1f}%",
            "htop.top_name": top.name,
            "htop.top_cpu": round(top.cpu, 2),
        }
