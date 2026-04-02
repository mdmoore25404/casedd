"""Container runtime getter for Docker, Podman, and containerd.

This getter auto-detects a supported runtime CLI and publishes normalized
container status keys under the ``containers.*`` namespace.

Store keys written:
    - ``containers.available`` (float)
    - ``containers.runtime`` (str)
    - ``containers.count_total`` (float)
    - ``containers.count_running`` (float)
    - ``containers.count_exited`` (float)
    - ``containers.count_paused`` (float)
    - ``containers.rows`` (str) -- newline-delimited ``name|summary`` rows
    - ``containers.<index>.*`` (top N per-container fields)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import shutil
import subprocess
from typing import Literal

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)

_RUNTIME_AUTO = "auto"
_UNAVAILABLE_ROW = "No runtime available|Install docker/podman/ctr or grant access"
_EMPTY_ROW = "No containers|Runtime reachable but no containers found"
_DOCKER_LOGO_PATH = "assets/docker/docker-official-logo.png"
_PODMAN_LOGO_PATH = "assets/docker/podman-official-logo.webp"

RuntimeName = Literal["docker", "podman", "containerd"]


@dataclass(frozen=True)
class _RuntimeBackend:
    """Selected runtime backend command metadata."""

    name: RuntimeName
    command: str


@dataclass(frozen=True)
class _ContainerRow:
    """Normalized container row."""

    name: str
    image: str
    status: str
    uptime: str
    health: str


class ContainersGetter(BaseGetter):
    """Collect runtime container status from Docker, Podman, or containerd.

    Args:
        store: Shared data store instance.
        interval: Poll interval in seconds.
        runtime: Preferred runtime (``auto|docker|podman|containerd``).
        max_items: Maximum indexed per-container fields emitted.
    """

    def __init__(
        self,
        store: DataStore,
        interval: float = 8.0,
        runtime: str = _RUNTIME_AUTO,
        max_items: int = 12,
    ) -> None:
        """Initialize runtime preference and command discovery."""
        super().__init__(store, interval)
        self._runtime = runtime.strip().lower() or _RUNTIME_AUTO
        self._max_items = max(1, max_items)
        self._warned_unavailable = False

        self._docker_cmd = shutil.which("docker")
        self._podman_cmd = shutil.which("podman")
        self._ctr_cmd = shutil.which("ctr")

    async def fetch(self) -> dict[str, StoreValue]:
        """Collect one container runtime sample."""
        return await asyncio.to_thread(self._sample)

    def _sample(self) -> dict[str, StoreValue]:
        """Blocking runtime sample implementation."""
        backend = self._select_backend()
        if backend is None:
            if not self._warned_unavailable:
                _log.info(
                    "Containers getter inactive: no accessible docker/podman/ctr runtime found"
                )
                self._warned_unavailable = True
            return _unavailable_payload()

        rows = self._collect_rows(backend)
        if rows is None:
            return _unavailable_payload(backend.name)
        if not rows:
            return _empty_payload(backend.name)

        return self._build_payload(backend.name, rows)

    def _select_backend(self) -> _RuntimeBackend | None:
        """Return the preferred available runtime backend."""
        backends: tuple[_RuntimeBackend, ...] = (
            _RuntimeBackend(name="docker", command=self._docker_cmd or ""),
            _RuntimeBackend(name="podman", command=self._podman_cmd or ""),
            _RuntimeBackend(name="containerd", command=self._ctr_cmd or ""),
        )

        if self._runtime != _RUNTIME_AUTO:
            for backend in backends:
                if backend.name == self._runtime and backend.command:
                    return backend
            return None

        for backend in backends:
            if backend.command:
                return backend
        return None

    def _collect_rows(self, backend: _RuntimeBackend) -> list[_ContainerRow] | None:
        """Collect normalized rows for the chosen runtime backend."""
        if backend.name in {"docker", "podman"}:
            text = self._run_command(
                [
                    backend.command,
                    "ps",
                    "-a",
                    "--format",
                    "{{.Names}}|{{.Status}}|{{.Image}}",
                ]
            )
            if text is None:
                return None
            return _parse_docker_like_rows(text)

        containers_text = self._run_command([backend.command, "containers", "list"])
        tasks_text = self._run_command([backend.command, "tasks", "list"])
        if containers_text is None or tasks_text is None:
            return None
        return _parse_containerd_rows(containers_text, tasks_text)

    def _build_payload(
        self,
        runtime: RuntimeName,
        rows: list[_ContainerRow],
    ) -> dict[str, StoreValue]:
        """Build store payload from normalized row data."""
        running = 0
        exited = 0
        paused = 0
        rendered: list[str] = []
        payload: dict[str, StoreValue] = {
            "containers.available": 1.0,
            "containers.runtime": runtime,
            "containers.logo_path": _runtime_logo_path(runtime),
            "containers.count_total": float(len(rows)),
        }

        for idx, row in enumerate(rows, start=1):
            status_lower = row.status.lower()
            if status_lower == "running":
                running += 1
            elif status_lower == "paused":
                paused += 1
            else:
                exited += 1

            summary = f"{row.status} | UP {row.uptime} | Health {row.health} | {row.image}"
            rendered.append(f"{row.name}|{summary}")

            if idx <= self._max_items:
                base = f"containers.{idx}"
                payload[f"{base}.name"] = row.name
                payload[f"{base}.status"] = row.status
                payload[f"{base}.uptime"] = row.uptime
                payload[f"{base}.health"] = row.health
                payload[f"{base}.image"] = row.image

        payload["containers.count_running"] = float(running)
        payload["containers.count_exited"] = float(exited)
        payload["containers.count_paused"] = float(paused)
        payload["containers.rows"] = "\n".join(rendered)
        return payload

    @staticmethod
    def _run_command(args: list[str]) -> str | None:
        """Run backend command and return stdout text when successful."""
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
            _log.debug("Container runtime command failed: %s", args, exc_info=True)
            return None
        return proc.stdout.strip()


def _parse_docker_like_rows(text: str) -> list[_ContainerRow]:
    """Parse docker/podman rows from ``name|status|image`` format."""
    rows: list[_ContainerRow] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split("|", maxsplit=2)
        if len(parts) != 3:
            continue
        name = parts[0].strip()
        status_text = parts[1].strip()
        image = parts[2].strip()
        rows.append(
            _ContainerRow(
                name=name or "unknown",
                image=image or "unknown",
                status=_status_from_runtime_text(status_text),
                uptime=_uptime_from_runtime_text(status_text),
                health=_health_from_runtime_text(status_text),
            )
        )
    return rows


def _parse_containerd_rows(
    containers_text: str,
    tasks_text: str,
) -> list[_ContainerRow]:
    """Parse ``ctr`` output into normalized rows.

    ``ctr containers list`` provides names/images and ``ctr tasks list``
    identifies currently running tasks.
    """
    running_names: set[str] = set()
    for raw in tasks_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("TASK"):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        if parts[2].upper() == "RUNNING":
            running_names.add(parts[0])

    rows: list[_ContainerRow] = []
    for raw in containers_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("CONTAINER"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[0]
        image = parts[1]
        status = "Running" if name in running_names else "Exited"
        rows.append(
            _ContainerRow(
                name=name,
                image=image,
                status=status,
                uptime="unknown",
                health="unknown",
            )
        )
    return rows


def _status_from_runtime_text(status: str) -> str:
    """Normalize runtime status text into concise labels."""
    lower = status.strip().lower()
    if lower.startswith("up"):
        return "Running"
    if lower.startswith("paused"):
        return "Paused"
    if lower.startswith("created"):
        return "Created"
    if lower.startswith(("exited", "stopped")):
        return "Exited"
    return "Unknown"


def _uptime_from_runtime_text(status: str) -> str:
    """Extract an uptime phrase from runtime status text when available."""
    lower = status.strip().lower()
    if lower.startswith("up "):
        text = status.strip()[3:]
        if "(" in text:
            text = text.split("(", maxsplit=1)[0].strip()
        return text or "running"
    if lower.startswith("exited") and " ago" in lower:
        return status.strip().split(")", maxsplit=1)[-1].strip()
    return "n/a"


def _health_from_runtime_text(status: str) -> str:
    """Extract health marker from runtime status string."""
    lower = status.strip().lower()
    if "healthy" in lower:
        return "healthy"
    if "unhealthy" in lower:
        return "unhealthy"
    if "starting" in lower:
        return "starting"
    return "unknown"


def _unavailable_payload(runtime: str = "unavailable") -> dict[str, StoreValue]:
    """Return payload when no runtime backend is available."""
    return {
        "containers.available": 0.0,
        "containers.runtime": runtime,
        "containers.logo_path": _runtime_logo_path(runtime),
        "containers.count_total": 0.0,
        "containers.count_running": 0.0,
        "containers.count_exited": 0.0,
        "containers.count_paused": 0.0,
        "containers.rows": _UNAVAILABLE_ROW,
    }


def _empty_payload(runtime: RuntimeName) -> dict[str, StoreValue]:
    """Return payload when runtime is reachable but has no containers."""
    return {
        "containers.available": 1.0,
        "containers.runtime": runtime,
        "containers.logo_path": _runtime_logo_path(runtime),
        "containers.count_total": 0.0,
        "containers.count_running": 0.0,
        "containers.count_exited": 0.0,
        "containers.count_paused": 0.0,
        "containers.rows": _EMPTY_ROW,
    }


def _runtime_logo_path(runtime: str) -> str:
    """Return the local logo asset path for the active runtime."""
    normalized = runtime.strip().lower()
    if normalized == "podman":
        return _PODMAN_LOGO_PATH
    if normalized == "docker":
        return _DOCKER_LOGO_PATH
    return "assets/casedd-logo.png"
