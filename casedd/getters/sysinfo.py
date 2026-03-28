"""System information getter (neofetch-style).

Collects static and semi-static system facts about the host and publishes
them under the ``sysinfo.*`` namespace.  Designed for a long refresh interval
(default 30 s) since most data changes only on reboot or package upgrades.

Store keys written:
    - ``sysinfo.hostname``  (str) -- machine hostname
    - ``sysinfo.os``        (str) -- OS pretty name from /etc/os-release
    - ``sysinfo.kernel``    (str) -- kernel release string
    - ``sysinfo.uptime``    (str) -- uptime formatted as "Xd HH:MM" or "HH:MM"
    - ``sysinfo.cpu_model`` (str) -- CPU model name
    - ``sysinfo.cpu_cores`` (str) -- e.g. "4c / 8t"
    - ``sysinfo.memory``    (str) -- e.g. "3.2G / 16.0G"
    - ``sysinfo.disk_root`` (str) -- root disk usage e.g. "42.0G / 120.0G"
    - ``sysinfo.ip``        (str) -- first non-loopback IPv4 address
    - ``sysinfo.rows``      (str) -- newline-delimited "Label|Value" for renderer
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import platform
import socket
import time

import psutil

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)


class SysinfoGetter(BaseGetter):
    """Getter producing neofetch-style system information rows."""

    def __init__(self, store: DataStore, interval: float = 30.0) -> None:
        """Initialise the system info getter.

        Args:
            store: Shared data store instance.
            interval: Poll interval in seconds (default: 30.0).
        """
        super().__init__(store, interval)

    async def fetch(self) -> dict[str, StoreValue]:
        """Collect one system info snapshot.

        Returns:
            Dict of ``sysinfo.*`` store updates.
        """
        return await asyncio.to_thread(self._sample)

    def _sample(self) -> dict[str, StoreValue]:
        """Blocking implementation: gather host system facts.

        Returns:
            Store update dict with all ``sysinfo.*`` keys.
        """
        hostname = socket.gethostname()
        os_name = _read_os_name()
        kernel = platform.release()
        uptime = _format_uptime(time.time() - psutil.boot_time())
        cpu_model = _read_cpu_model()

        logical = psutil.cpu_count(logical=True) or 1
        physical = psutil.cpu_count(logical=False) or logical
        cpu_cores = f"{physical}c / {logical}t"

        vm = psutil.virtual_memory()
        memory = f"{vm.used / 1e9:.1f}G / {vm.total / 1e9:.1f}G"

        try:
            du = psutil.disk_usage("/")
            disk = f"{du.used / 1e9:.1f}G / {du.total / 1e9:.1f}G"
        except OSError:
            _log.debug("Could not read root disk usage")
            disk = "N/A"

        ip = _local_ip()

        pairs: list[tuple[str, str]] = [
            ("Hostname", hostname),
            ("OS", os_name),
            ("Kernel", kernel),
            ("Uptime", uptime),
            ("CPU", cpu_model),
            ("Cores", cpu_cores),
            ("Memory", memory),
            ("Disk (/)", disk),
            ("IP", ip),
        ]
        rows_str = "\n".join(f"{label}|{value}" for label, value in pairs)

        return {
            "sysinfo.hostname": hostname,
            "sysinfo.os": os_name,
            "sysinfo.kernel": kernel,
            "sysinfo.uptime": uptime,
            "sysinfo.cpu_model": cpu_model,
            "sysinfo.cpu_cores": cpu_cores,
            "sysinfo.memory": memory,
            "sysinfo.disk_root": disk,
            "sysinfo.ip": ip,
            "sysinfo.rows": rows_str,
        }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _read_os_name() -> str:
    """Read the OS pretty name from ``/etc/os-release``, fallback to platform.

    Returns:
        Human-readable OS name string.
    """
    try:
        os_release = Path("/etc/os-release").read_text(encoding="utf-8")
        for line in os_release.splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.system()


def _read_cpu_model() -> str:
    """Read CPU model name from ``/proc/cpuinfo``, fallback to platform.

    Returns:
        CPU model name string.
    """
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8")
        for line in cpuinfo.splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "Unknown"


def _format_uptime(seconds: float) -> str:
    """Format an uptime duration in seconds to a human-readable string.

    Args:
        seconds: Elapsed uptime in seconds.

    Returns:
        String in the form ``"Xd HH:MM"`` (days present) or ``"HH:MM"``.
    """
    s = int(seconds)
    days, s = divmod(s, 86400)
    hours, s = divmod(s, 3600)
    minutes = s // 60
    if days:
        return f"{days}d {hours:02d}:{minutes:02d}"
    return f"{hours:02d}:{minutes:02d}"


def _local_ip() -> str:
    """Find the first non-loopback IPv4 address, or ``"N/A"``.

    Returns:
        IPv4 address string, or ``"N/A"`` if none found.
    """
    for iface, addrs in psutil.net_if_addrs().items():
        if iface == "lo":
            continue
        for addr in addrs:
            if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                return addr.address
    return "N/A"
