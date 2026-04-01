"""NVIDIA GPU data getter.

Polls GPU stats via ``nvidia-smi --query-gpu`` and publishes them to the
data store under the ``nvidia.*`` namespace.

If ``nvidia-smi`` is absent or returns an error this getter disables itself
cleanly — all calls become no-ops. No root or special permissions required.

Store keys written:
    - ``nvidia.name`` (str) -- Primary GPU model name
    - ``nvidia.percent`` (float) -- GPU utilisation 0-100
    - ``nvidia.temperature`` (float) — GPU temperature in °C
    - ``nvidia.memory_used_mb`` (float) — VRAM used in MB
    - ``nvidia.memory_free_mb`` (float) — VRAM free in MB
    - ``nvidia.memory_total_mb`` (float) — VRAM total in MB
    - ``nvidia.power_w`` (float) — GPU power draw in Watts
"""

import asyncio
import logging
import shutil
import subprocess
from typing import TypedDict

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)

# nvidia-smi query fields — order must match parsed columns below.
_QUERY = (
    "index,name,utilization.gpu,temperature.gpu,memory.used,memory.total,power.draw"
)


class _GpuRow(TypedDict):
    """Parsed single-GPU row from ``nvidia-smi``."""

    idx: int
    name: str
    percent: float
    temperature: float
    memory_used_mb: float
    memory_total_mb: float
    memory_free_mb: float
    power_w: float


class GpuGetter(BaseGetter):
    """Getter for NVIDIA GPU metrics via ``nvidia-smi``.

    Disables itself silently if ``nvidia-smi`` is not on ``PATH``.

    Args:
        store: Shared data store instance.
        interval: Poll interval in seconds (default: 5.0).
    """

    def __init__(self, store: DataStore, interval: float = 5.0) -> None:
        """Initialise the GPU getter.

        Probes for ``nvidia-smi`` availability immediately. If absent the
        getter is permanently disabled.

        Args:
            store: The shared :class:`~casedd.data_store.DataStore`.
            interval: Seconds between each poll (default: 5.0).
        """
        super().__init__(store, interval)
        self._enabled: bool = shutil.which("nvidia-smi") is not None
        if self._enabled:
            _log.info("nvidia-smi found — GPU getter active.")
        else:
            _log.info("nvidia-smi not found — GPU getter disabled (no-op).")

    async def fetch(self) -> dict[str, StoreValue]:
        """Sample GPU metrics via ``nvidia-smi``.

        Returns an empty dict (no-op) if the getter is disabled.

        Returns:
            Dict with ``nvidia.*`` keys, or empty dict if disabled.
        """
        if not self._enabled:
            return {}
        return await asyncio.to_thread(self._sample)

    def _sample(self) -> dict[str, StoreValue]:
        """Run ``nvidia-smi`` and parse its output.

        Disables the getter permanently on repeated failures.

        Returns:
            Dict of store updates, or empty dict on parse error.
        """
        try:
            result = subprocess.run(  # noqa: S603 — fixed arg list, no user input
                ["nvidia-smi", f"--query-gpu={_QUERY}", "--format=csv,noheader,nounits"],  # noqa: S607 — well-known system path
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError) as exc:
            _log.warning("nvidia-smi failed (%s) — disabling GPU getter.", exc)
            self._enabled = False
            return {}

        lines = [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
        if not lines:
            _log.warning("Unexpected nvidia-smi output: %r", result.stdout)
            return {}

        data: dict[str, StoreValue] = {}
        gpu_rows: list[_GpuRow] = []
        for line in lines:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) != 7:
                continue
            try:
                gpu_idx = int(parts[0])
                row: _GpuRow = {
                    "idx": gpu_idx,
                    "name": parts[1],
                    "percent": float(parts[2]),
                    "temperature": float(parts[3]),
                    "memory_used_mb": float(parts[4]),
                    "memory_total_mb": float(parts[5]),
                    "memory_free_mb": float(parts[5]) - float(parts[4]),
                    "power_w": float(parts[6]),
                }
                gpu_rows.append(row)
            except ValueError:
                # Field may be "[N/A]" — leave the key absent rather than emit garbage
                _log.debug("Could not parse nvidia-smi row: %r", line)

        if not gpu_rows:
            return {}

        data["nvidia.gpu_count"] = float(len(gpu_rows))
        for row in gpu_rows:
            idx = row["idx"]
            data[f"nvidia.{idx}.name"] = row["name"]
            data[f"nvidia.{idx}.percent"] = row["percent"]
            data[f"nvidia.{idx}.temperature"] = row["temperature"]
            data[f"nvidia.{idx}.memory_used_mb"] = row["memory_used_mb"]
            data[f"nvidia.{idx}.memory_free_mb"] = row["memory_free_mb"]
            data[f"nvidia.{idx}.memory_total_mb"] = row["memory_total_mb"]
            data[f"nvidia.{idx}.power_w"] = row["power_w"]

        # Backward-compatible primary keys map to GPU index 0 if present,
        # otherwise the first reported GPU.
        primary = next((row for row in gpu_rows if row["idx"] == 0), gpu_rows[0])
        data["nvidia.name"] = primary["name"]
        data["nvidia.percent"] = primary["percent"]
        data["nvidia.temperature"] = primary["temperature"]
        data["nvidia.memory_used_mb"] = primary["memory_used_mb"]
        data["nvidia.memory_free_mb"] = primary["memory_free_mb"]
        data["nvidia.memory_total_mb"] = primary["memory_total_mb"]
        data["nvidia.power_w"] = primary["power_w"]
        # Compute VRAM utilisation % for the primary GPU.
        if primary["memory_total_mb"] > 0:
            data["nvidia.memory_percent"] = round(
                (primary["memory_used_mb"] / primary["memory_total_mb"]) * 100.0, 2
            )

        data["nvidia.total_memory_used_mb"] = sum(row["memory_used_mb"] for row in gpu_rows)
        data["nvidia.total_memory_free_mb"] = sum(row["memory_free_mb"] for row in gpu_rows)
        data["nvidia.total_memory_mb"] = sum(row["memory_total_mb"] for row in gpu_rows)
        return data
