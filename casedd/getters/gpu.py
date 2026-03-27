"""NVIDIA GPU data getter.

Polls GPU stats via ``nvidia-smi --query-gpu`` and publishes them to the
data store under the ``nvidia.*`` namespace.

If ``nvidia-smi`` is absent or returns an error this getter disables itself
cleanly — all calls become no-ops. No root or special permissions required.

Store keys written:
    - ``nvidia.percent`` (float) -- GPU utilisation 0-100
    - ``nvidia.temperature`` (float) — GPU temperature in °C
    - ``nvidia.memory_used_mb`` (float) — VRAM used in MB
    - ``nvidia.memory_total_mb`` (float) — VRAM total in MB
    - ``nvidia.power_w`` (float) — GPU power draw in Watts
"""

import asyncio
import logging
import shutil
import subprocess

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)

# nvidia-smi query fields — order must match _KEYS below
_QUERY = "utilization.gpu,temperature.gpu,memory.used,memory.total,power.draw"
_KEYS = ("nvidia.percent", "nvidia.temperature", "nvidia.memory_used_mb",
         "nvidia.memory_total_mb", "nvidia.power_w")


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
            result = subprocess.run(  # noqa: S603,S607 — fixed command, no user input; nvidia-smi is a well-known path
                ["nvidia-smi", f"--query-gpu={_QUERY}", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError) as exc:
            _log.warning("nvidia-smi failed (%s) — disabling GPU getter.", exc)
            self._enabled = False
            return {}

        parts = [p.strip() for p in result.stdout.strip().split(",")]
        if len(parts) != len(_KEYS):
            _log.warning("Unexpected nvidia-smi output: %r", result.stdout)
            return {}

        data: dict[str, StoreValue] = {}
        for key, raw in zip(_KEYS, parts, strict=True):
            try:
                data[key] = float(raw)
            except ValueError:
                # Field may be "[N/A]" — leave the key absent rather than emit garbage
                _log.debug("Could not parse nvidia-smi field %s=%r", key, raw)
        return data
