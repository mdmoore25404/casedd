"""Tests for speedtest getter field extraction."""

from __future__ import annotations

from casedd.data_store import DataStore
from casedd.getters.speedtest import SpeedtestGetter


def test_extract_metrics_emits_split_last_run_fields() -> None:
    """Speedtest getter emits display-friendly split timestamp fields."""
    getter = SpeedtestGetter(DataStore(), passive=True)

    payload = {
        "download": {"bandwidth": 125_000_000.0},
        "upload": {"bandwidth": 37_500_000.0},
        "ping": {"latency": 17.8, "jitter": 1.6},
        "server": {
            "id": 1234,
            "name": "Example",
            "location": "Ashburn",
            "country": "US",
            "host": "example.net",
        },
    }

    values = getter._extract_metrics(payload)

    assert values["speedtest.last_run"]
    assert values["speedtest.last_run_date"]
    assert values["speedtest.last_run_time"]
    assert "\n" in str(values["speedtest.last_run_display"])
