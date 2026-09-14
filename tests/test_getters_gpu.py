"""Tests for NVIDIA GPU getter parsing."""

from __future__ import annotations

from unittest.mock import patch

from casedd.data_store import DataStore
from casedd.getters.gpu import GpuGetter


class _CompletedProcess:
    """Simple subprocess result stub for GPU getter tests."""

    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def test_gpu_getter_emits_memory_free_fields() -> None:
    """GPU getter derives free VRAM for primary and indexed keys."""
    store = DataStore()
    with (
        patch("casedd.getters.gpu.shutil.which", return_value="/usr/bin/nvidia-smi"),
        patch(
            "casedd.getters.gpu.subprocess.run",
            return_value=_CompletedProcess(
                "0, NVIDIA GeForce RTX 5070 Ti, 12, 39, 14278, 16303, 12\n"
            ),
        ),
    ):
        getter = GpuGetter(store)
        payload = getter._sample()

    assert payload["nvidia.name"] == "NVIDIA GeForce RTX 5070 Ti"
    assert payload["nvidia.0.name"] == "NVIDIA GeForce RTX 5070 Ti"
    assert payload["nvidia.memory_used_mb"] == 14278.0
    assert payload["nvidia.memory_total_mb"] == 16303.0
    assert payload["nvidia.memory_free_mb"] == 2025.0
    assert payload["nvidia.0.memory_free_mb"] == 2025.0
    assert payload["nvidia.total_memory_free_mb"] == 2025.0


def test_gpu_getter_handles_na_utilization_tokens() -> None:
    """Rows with [N/A] util should still emit stable numeric nvidia.* keys."""
    store = DataStore()
    with (
        patch("casedd.getters.gpu.shutil.which", return_value="/usr/bin/nvidia-smi"),
        patch(
            "casedd.getters.gpu.subprocess.run",
            return_value=_CompletedProcess(
                "0, NVIDIA GeForce RTX 5070 Ti, [N/A], 39, 14278, 16303, 12\n"
            ),
        ),
    ):
        getter = GpuGetter(store)
        payload = getter._sample()

    assert payload["nvidia.percent"] == 0.0
    assert payload["nvidia.0.percent"] == 0.0
    assert payload["nvidia.memory_total_mb"] == 16303.0


def test_gpu_getter_emits_per_gpu_memory_percent_for_multi_gpu_hosts() -> None:
    """Each GPU row gets its own nvidia.{idx}.memory_percent, not just the primary.

    Regression guard: on dual-GPU hosts, templates showing a second GPU's
    VRAM gauge/value need nvidia.1.memory_percent (etc.) populated, not just
    the back-compat nvidia.memory_percent alias for GPU 0.
    """
    store = DataStore()
    with (
        patch("casedd.getters.gpu.shutil.which", return_value="/usr/bin/nvidia-smi"),
        patch(
            "casedd.getters.gpu.subprocess.run",
            return_value=_CompletedProcess(
                "0, NVIDIA GeForce RTX 5070 Ti, 12, 39, 8, 16303, 13\n"
                "1, NVIDIA GeForce RTX 5070 Ti, 0, 44, 5379, 16303, 33\n"
            ),
        ),
    ):
        getter = GpuGetter(store)
        payload = getter._sample()

    assert payload["nvidia.gpu_count"] == 2.0
    assert payload["nvidia.0.memory_percent"] == round((8 / 16303) * 100.0, 2)
    assert payload["nvidia.1.memory_percent"] == round((5379 / 16303) * 100.0, 2)
    # Back-compat primary alias still maps to GPU 0.
    assert payload["nvidia.memory_percent"] == payload["nvidia.0.memory_percent"]
