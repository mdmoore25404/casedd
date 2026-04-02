"""Virtual machine metrics getter for KVM/libvirt hosts.

Collects VM status and lightweight telemetry via ``virsh``. The getter is
optional and degrades gracefully when ``virsh``/libvirt is unavailable.

Store keys written:
    - ``vms.available`` (float) -- 1 when virsh polling is active, else 0
    - ``vms.mode`` (str) -- ``active``, ``passive``, or ``unavailable``
    - ``vms.count_total`` (float)
    - ``vms.count_running`` (float)
    - ``vms.count_paused`` (float)
    - ``vms.count_shutoff`` (float)
    - ``vms.total_allocated_mib`` (float)
    - ``vms.total_actual_mib`` (float)
    - ``vms.total_cpu_percent`` (float)
    - ``vms.rows`` (str) -- newline-delimited ``name|summary`` table rows
    - ``vms.<index>.*`` (per-VM fields for top N VMs)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from pathlib import Path
import shutil
import subprocess
import time

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)

_UNAVAILABLE_ROWS = "libvirt unavailable|Install virsh or use passive push mode"
_NO_VM_ROWS = "No virtual machines|No domains returned by libvirt"


@dataclass(frozen=True)
class _Domain:
    """One domain row from ``virsh list --all``."""

    name: str
    state: str


@dataclass(frozen=True)
class _DomainInfo:
    """Parsed subset of ``virsh dominfo`` output."""

    state: str
    os_type: str
    vcpus: int
    cpu_time_seconds: float
    max_memory_mib: float
    used_memory_mib: float


@dataclass
class _Summary:
    """Running aggregate counters for one sample pass."""

    running: int = 0
    paused: int = 0
    shutoff: int = 0
    total_allocated_mib: float = 0.0
    total_actual_mib: float = 0.0
    total_cpu_percent: float = 0.0


@dataclass(frozen=True)
class _DomainSample:
    """One sampled VM row with aggregate contribution."""

    row_text: str
    summary: _Summary
    state_label: str
    os_type: str
    allocated_mib: float
    actual_mib: float
    cpu_percent: float
    uptime_seconds: int


class VmGetter(BaseGetter):
    """Getter for KVM/libvirt VM status and resource telemetry via ``virsh``.

    Args:
        store: Shared data store instance.
        interval: Poll interval in seconds.
        passive: When true, emit only passive-mode status and never run virsh.
        command: Virsh binary name or absolute path.
        max_items: Maximum number of indexed per-VM fields to emit.
    """

    def __init__(
        self,
        store: DataStore,
        interval: float = 10.0,
        passive: bool = False,
        command: str = "virsh",
        max_items: int = 8,
    ) -> None:
        """Initialise VM getter settings and detect command availability."""
        super().__init__(store, interval)
        self._passive = passive
        self._command = command
        self._max_items = max(1, max_items)
        self._warned_unavailable = False
        self._cpu_cache: dict[str, tuple[float, float]] = {}
        self._uptime_started_at: dict[str, float] = {}

        discovered = shutil.which(command)
        if discovered is not None:
            self._virsh_path: str | None = discovered
        elif Path(command).is_file():
            self._virsh_path = command
        else:
            self._virsh_path = None

        self._enabled = (not passive) and (self._virsh_path is not None)

        if self._passive:
            _log.info("VM getter in passive mode; expecting external pushes under vms.*")
        elif self._enabled:
            _log.info("VM getter active via virsh (%s)", self._virsh_path)
        else:
            _log.info("VM getter disabled (virsh not found); emitting unavailable fallback")

    async def fetch(self) -> dict[str, StoreValue]:
        """Collect one VM telemetry sample.

        Returns:
            Mapping containing ``vms.*`` keys.
        """
        return await asyncio.to_thread(self._sample)

    def _sample(self) -> dict[str, StoreValue]:
        """Blocking VM sample implementation using virsh CLI."""
        if self._passive:
            return {
                "vms.available": 0.0,
                "vms.mode": "passive",
                "vms.rows": "Passive mode|Waiting for external vms.* pushes",
            }

        if not self._enabled or self._virsh_path is None:
            if not self._warned_unavailable:
                _log.info(
                    "VM getter inactive (virsh unavailable); install libvirt tools "
                    "or set CASEDD_VMS_PASSIVE=1 for push-only mode"
                )
                self._warned_unavailable = True
            return self._unavailable_payload()

        domains = self._list_domains()
        if domains is None:
            return self._unavailable_payload()
        if not domains:
            return self._empty_payload()

        payload: dict[str, StoreValue] = {
            "vms.available": 1.0,
            "vms.mode": "active",
            "vms.count_total": float(len(domains)),
        }
        summary = _Summary()
        rows: list[str] = []
        now = time.monotonic()

        for index, domain in enumerate(domains, start=1):
            domain_sample = self._sample_domain(domain, now)
            self._write_indexed_domain_fields(payload, domain_sample, domain.name, index)
            summary.running += domain_sample.summary.running
            summary.paused += domain_sample.summary.paused
            summary.shutoff += domain_sample.summary.shutoff
            summary.total_allocated_mib += domain_sample.summary.total_allocated_mib
            summary.total_actual_mib += domain_sample.summary.total_actual_mib
            summary.total_cpu_percent += domain_sample.summary.total_cpu_percent
            rows.append(f"{domain.name}|{domain_sample.row_text}")
        payload["vms.count_running"] = float(summary.running)
        payload["vms.count_paused"] = float(summary.paused)
        payload["vms.count_shutoff"] = float(summary.shutoff)
        payload["vms.total_allocated_mib"] = round(summary.total_allocated_mib, 2)
        payload["vms.total_actual_mib"] = round(summary.total_actual_mib, 2)
        payload["vms.total_cpu_percent"] = round(summary.total_cpu_percent, 2)
        payload["vms.rows"] = "\n".join(rows)
        return payload

    def _sample_domain(
        self,
        domain: _Domain,
        now: float,
    ) -> _DomainSample:
        """Collect one domain's metrics and aggregate contribution."""
        info = self._domain_info(domain)
        effective_state = info.state if info is not None else domain.state
        state_label = _state_label(effective_state)
        os_type = info.os_type if info is not None else "unknown"
        allocated_mib = info.max_memory_mib if info is not None else 0.0
        actual_mib = info.used_memory_mib if info is not None else 0.0
        cpu_percent = self._cpu_percent(domain.name, info, now)
        uptime_seconds = self._uptime_seconds(domain.name, effective_state, now)

        row_text = (
            f"{state_label} | CPU {cpu_percent:.1f}% | MEM {actual_mib:.0f}/"
            f"{allocated_mib:.0f} MiB | UP {format_uptime(uptime_seconds)} | {os_type}"
        )

        summary = _Summary(
            total_allocated_mib=allocated_mib,
            total_actual_mib=actual_mib,
            total_cpu_percent=cpu_percent,
        )
        if state_label == "Running":
            summary.running = 1
        elif state_label == "Paused":
            summary.paused = 1
        elif state_label == "Shut off":
            summary.shutoff = 1
        return _DomainSample(
            row_text=row_text,
            summary=summary,
            state_label=state_label,
            os_type=os_type,
            allocated_mib=allocated_mib,
            actual_mib=actual_mib,
            cpu_percent=cpu_percent,
            uptime_seconds=uptime_seconds,
        )

    def _write_indexed_domain_fields(
        self,
        payload: dict[str, StoreValue],
        sample: _DomainSample,
        domain_name: str,
        index: int,
    ) -> None:
        """Write per-domain fields for the first ``max_items`` domains."""
        if index > self._max_items:
            return
        base = f"vms.{index}"
        payload[f"{base}.name"] = domain_name
        payload[f"{base}.state"] = sample.state_label
        payload[f"{base}.cpu_percent"] = round(sample.cpu_percent, 2)
        payload[f"{base}.memory_allocated_mib"] = round(sample.allocated_mib, 2)
        payload[f"{base}.memory_actual_mib"] = round(sample.actual_mib, 2)
        payload[f"{base}.uptime_seconds"] = float(sample.uptime_seconds)
        payload[f"{base}.uptime"] = format_uptime(sample.uptime_seconds)
        payload[f"{base}.os_type"] = sample.os_type

    def _list_domains(self) -> list[_Domain] | None:
        """Return all libvirt domains with parsed state labels.

        Returns:
            Domain list on success, empty list for no domains, or ``None`` on
            command failure.
        """
        output = self._run_command([self._virsh_path or self._command, "list", "--all"])
        if output is None:
            return None

        parsed: list[_Domain] = []
        for raw in output.splitlines():
            line = raw.strip()
            if not line or line.startswith("Id"):
                continue
            if line and set(line) == {"-"}:
                continue
            parts = line.split(maxsplit=2)
            if len(parts) < 3:
                continue
            name = parts[1].strip()
            state = parts[2].strip().lower()
            parsed.append(_Domain(name=name, state=state))
        return parsed

    def _domain_info(self, domain: _Domain) -> _DomainInfo | None:
        """Fetch detailed telemetry for one VM domain via ``virsh dominfo``."""
        output = self._run_command(
            [self._virsh_path or self._command, "dominfo", domain.name],
        )
        if output is None:
            return None

        mapping = _parse_key_value_lines(output)
        state = mapping.get("state", domain.state).strip().lower()
        os_type = mapping.get("os type", "unknown").strip().lower() or "unknown"
        vcpus = _parse_int(mapping.get("cpu(s)", ""), default=1)
        cpu_time_seconds = _parse_cpu_seconds(mapping.get("cpu time", ""))
        max_memory_mib = _kib_to_mib(mapping.get("max memory", ""))
        used_memory_mib = _kib_to_mib(mapping.get("used memory", ""))

        return _DomainInfo(
            state=state,
            os_type=os_type,
            vcpus=max(1, vcpus),
            cpu_time_seconds=max(0.0, cpu_time_seconds),
            max_memory_mib=max(0.0, max_memory_mib),
            used_memory_mib=max(0.0, used_memory_mib),
        )

    def _cpu_percent(self, domain_name: str, info: _DomainInfo | None, now: float) -> float:
        """Compute per-domain CPU usage percent from cumulative CPU time."""
        if info is None or _state_label(info.state) != "Running":
            return 0.0

        previous = self._cpu_cache.get(domain_name)
        self._cpu_cache[domain_name] = (info.cpu_time_seconds, now)
        if previous is None:
            return 0.0

        prev_cpu_seconds, prev_time = previous
        elapsed = max(0.001, now - prev_time)
        cpu_delta = max(0.0, info.cpu_time_seconds - prev_cpu_seconds)
        cpu_percent = (cpu_delta / elapsed) * 100.0
        return min(100.0 * float(max(1, info.vcpus)), cpu_percent)

    def _uptime_seconds(self, domain_name: str, state: str, now: float) -> int:
        """Track coarse VM uptime from state transitions.

        Uses monotonic timestamps and resets when the VM is not running.
        """
        if _state_label(state) != "Running":
            self._cpu_cache.pop(domain_name, None)
            self._uptime_started_at.pop(domain_name, None)
            return 0

        started = self._uptime_started_at.get(domain_name)
        if started is None:
            self._uptime_started_at[domain_name] = now
            return 0
        return max(0, int(now - started))

    @staticmethod
    def _run_command(args: list[str]) -> str | None:
        """Run ``virsh`` command safely and return stdout text."""
        if not args:
            return None
        try:
            proc = subprocess.run(  # noqa: S603 -- fixed argv, shell disabled
                args,
                capture_output=True,
                text=True,
                timeout=8,
                check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            _log.debug("VM getter command failed: %s", args, exc_info=True)
            return None
        text = proc.stdout.strip()
        return text if text else ""

    @staticmethod
    def _unavailable_payload() -> dict[str, StoreValue]:
        """Payload used when virsh/libvirt are unavailable."""
        return {
            "vms.available": 0.0,
            "vms.mode": "unavailable",
            "vms.count_total": 0.0,
            "vms.count_running": 0.0,
            "vms.count_paused": 0.0,
            "vms.count_shutoff": 0.0,
            "vms.total_allocated_mib": 0.0,
            "vms.total_actual_mib": 0.0,
            "vms.total_cpu_percent": 0.0,
            "vms.rows": _UNAVAILABLE_ROWS,
        }

    @staticmethod
    def _empty_payload() -> dict[str, StoreValue]:
        """Payload used when libvirt is available but has no domains."""
        return {
            "vms.available": 1.0,
            "vms.mode": "active",
            "vms.count_total": 0.0,
            "vms.count_running": 0.0,
            "vms.count_paused": 0.0,
            "vms.count_shutoff": 0.0,
            "vms.total_allocated_mib": 0.0,
            "vms.total_actual_mib": 0.0,
            "vms.total_cpu_percent": 0.0,
            "vms.rows": _NO_VM_ROWS,
        }


def _parse_key_value_lines(output: str) -> dict[str, str]:
    """Parse ``Key: Value`` command output to lowercase key mapping."""
    result: dict[str, str] = {}
    for raw_line in output.splitlines():
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", maxsplit=1)
        result[key.strip().lower()] = value.strip()
    return result


def _parse_cpu_seconds(raw: str) -> float:
    """Parse dominfo CPU time value (e.g. ``123.4s``) into seconds."""
    cleaned = raw.strip().lower().removesuffix("s").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _parse_int(raw: str, default: int) -> int:
    """Extract first integer token from a free-form value string."""
    token = raw.strip().split(maxsplit=1)[0] if raw.strip() else ""
    try:
        return int(token)
    except ValueError:
        return default


def _kib_to_mib(raw: str) -> float:
    """Parse libvirt KiB memory field into MiB."""
    token = raw.strip().split(maxsplit=1)[0] if raw.strip() else ""
    try:
        return float(token) / 1024.0
    except ValueError:
        return 0.0


def _state_label(state: str) -> str:
    """Map libvirt state text into compact dashboard labels."""
    normalized = state.strip().lower()
    if normalized.startswith("running"):
        return "Running"
    if normalized.startswith("paused"):
        return "Paused"
    if normalized in {"shut off", "shutoff", "shutdown"}:
        return "Shut off"
    return normalized.title() if normalized else "Unknown"


def format_uptime(seconds: int) -> str:
    """Render uptime seconds as compact ``Xd HH:MM:SS`` text."""
    if seconds <= 0:
        return "00:00:00"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours:02}:{minutes:02}:{secs:02}"
    return f"{hours:02}:{minutes:02}:{secs:02}"
