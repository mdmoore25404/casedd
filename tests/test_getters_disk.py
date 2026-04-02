"""Tests for :mod:`casedd.getters.disk`."""

from __future__ import annotations

from types import SimpleNamespace

from casedd.data_store import DataStore
from casedd.getters.disk import DiskGetter


def test_disk_getter_emits_megabytes_per_second_and_aliases(monkeypatch: object) -> None:
    """Disk throughput should be reported in MB/s with legacy aliases preserved."""
    io_sequence = iter(
        (
            SimpleNamespace(read_bytes=1_000_000, write_bytes=2_000_000),
            SimpleNamespace(read_bytes=3_500_000, write_bytes=5_000_000),
        )
    )
    monotonic_values = iter((100.0, 102.0))

    monkeypatch.setattr(
        "casedd.getters.disk.psutil.disk_io_counters",
        lambda: next(io_sequence),
    )
    monkeypatch.setattr(
        "casedd.getters.disk.psutil.disk_usage",
        lambda mount: SimpleNamespace(percent=25.0, used=10, total=20, free=10),
    )
    monkeypatch.setattr(
        "casedd.getters.disk.time.monotonic",
        lambda: next(monotonic_values),
    )

    getter = DiskGetter(DataStore())
    payload = getter._sample("/")

    assert payload["disk.read_mb_s"] == 1.25
    assert payload["disk.write_mb_s"] == 1.5
    assert payload["disk.read_mbps"] == 1.25
    assert payload["disk.write_mbps"] == 1.5
