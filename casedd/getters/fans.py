"""Fan telemetry getter.

Polls host fan sensors via ``psutil.sensors_fans`` and, when available,
queries NVIDIA GPU fan speed via ``nvidia-smi``.

Store keys written:
    - ``fans.total.count`` (float)
    - ``fans.cpu.count`` / ``fans.system.count`` / ``fans.gpu.count`` (float)
    - ``fans.cpu.max_rpm`` / ``fans.system.max_rpm`` / ``fans.gpu.max_rpm`` (float)
    - ``fans.cpu.avg_rpm`` / ``fans.system.avg_rpm`` / ``fans.gpu.avg_rpm`` (float)
    - ``fans.<class>.<n>.rpm`` (float, per-fan values)

Compatibility key:
    - ``cpu.fan_rpm`` mirrors ``fans.cpu.max_rpm`` when available.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from typing import cast

import psutil

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)


class FanGetter(BaseGetter):
    """Getter for system, CPU, and GPU fan metrics.

    Args:
        store: Shared data store instance.
        interval: Poll interval in seconds (default: 3.0).
    """

    def __init__(self, store: DataStore, interval: float = 3.0) -> None:
        """Initialise the fan getter.

        Args:
            store: The shared :class:`~casedd.data_store.DataStore`.
            interval: Seconds between each poll (default: 3.0).
        """
        super().__init__(store, interval)
        self._nvidia_smi_path = shutil.which("nvidia-smi")
        self._nvidia_enabled = self._nvidia_smi_path is not None

    async def fetch(self) -> dict[str, StoreValue]:
        """Sample fan metrics.

        Returns:
            Dict containing ``fans.*`` keys.
        """
        return await asyncio.to_thread(self._sample)

    def _sample(self) -> dict[str, StoreValue]:
        """Collect fan data from psutil and optional nvidia-smi output.

        Returns:
            Dict of dotted store keys.
        """
        cpu_rpms: list[float] = []
        system_rpms: list[float] = []
        gpu_rpms: list[float] = []

        if hasattr(psutil, "sensors_fans"):
            try:
                fans_by_chip = psutil.sensors_fans()
            except Exception:
                _log.debug("psutil.sensors_fans failed", exc_info=True)
                fans_by_chip = {}
            self._collect_psutil_fans(
                cast("dict[str, list[object]]", fans_by_chip),
                cpu_rpms,
                system_rpms,
                gpu_rpms,
            )

        if self._nvidia_enabled:
            gpu_rpms.extend(self._read_nvidia_fan_percent())

        result = self._build_result(cpu_rpms, system_rpms, gpu_rpms)

        # Backward compatibility for existing templates that read cpu.fan_rpm.
        result["cpu.fan_rpm"] = result.get("fans.cpu.max_rpm", 0.0)
        return result

    @staticmethod
    def _collect_psutil_fans(
        fans_by_chip: dict[str, list[object]],
        cpu_rpms: list[float],
        system_rpms: list[float],
        gpu_rpms: list[float],
    ) -> None:
        """Classify psutil fan entries into CPU/system/GPU buckets.

        Args:
            fans_by_chip: Raw mapping returned by ``psutil.sensors_fans``.
            cpu_rpms: Output list for CPU fan values.
            system_rpms: Output list for system/chassis fan values.
            gpu_rpms: Output list for GPU fan values.
        """
        for chip, entries in fans_by_chip.items():
            chip_lower = chip.lower()
            for entry_obj in entries:
                # psutil returns named tuples with ``label`` and ``current``.
                label = str(getattr(entry_obj, "label", "")).lower()
                current_obj = getattr(entry_obj, "current", 0.0)
                try:
                    current = float(current_obj)
                except (TypeError, ValueError):
                    continue

                if current <= 0.0:
                    continue

                combined = f"{chip_lower} {label}"
                if "gpu" in combined or "nvidia" in combined:
                    gpu_rpms.append(current)
                elif "cpu" in combined or "processor" in combined:
                    cpu_rpms.append(current)
                else:
                    system_rpms.append(current)

    def _read_nvidia_fan_percent(self) -> list[float]:
        """Read NVIDIA per-GPU fan percentages via ``nvidia-smi``.

        Returns:
            List of per-GPU fan percentages as floats.
        """
        try:
            nvidia_smi = self._nvidia_smi_path
            if nvidia_smi is None:
                return []
            result = subprocess.run(  # noqa: S603 — fixed argument list, no shell
                [
                    nvidia_smi,
                    "--query-gpu=fan.speed",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
            # Keep the getter alive and just skip NVIDIA-specific data.
            _log.debug("nvidia-smi fan query failed", exc_info=True)
            return []

        values: list[float] = []
        for raw_line in result.stdout.strip().splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                values.append(float(line))
            except ValueError:
                # Some systems may emit "[N/A]" for fanless cards.
                continue
        return values

    @staticmethod
    def _build_result(
        cpu_rpms: list[float],
        system_rpms: list[float],
        gpu_rpms: list[float],
    ) -> dict[str, StoreValue]:
        """Build the final flattened fan telemetry payload.

        Args:
            cpu_rpms: CPU fan values.
            system_rpms: System/chassis fan values.
            gpu_rpms: GPU fan values.

        Returns:
            Flat key/value mapping for store updates.
        """
        result: dict[str, StoreValue] = {}

        fan_classes: list[tuple[str, list[float]]] = [
            ("cpu", cpu_rpms),
            ("system", system_rpms),
            ("gpu", gpu_rpms),
        ]

        total_count = 0
        for cls_name, values in fan_classes:
            total_count += len(values)
            result[f"fans.{cls_name}.count"] = float(len(values))
            result[f"fans.{cls_name}.max_rpm"] = max(values) if values else 0.0
            result[f"fans.{cls_name}.avg_rpm"] = (
                (sum(values) / len(values)) if values else 0.0
            )
            for idx, value in enumerate(values):
                result[f"fans.{cls_name}.{idx}.rpm"] = value

        result["fans.total.count"] = float(total_count)
        return result
