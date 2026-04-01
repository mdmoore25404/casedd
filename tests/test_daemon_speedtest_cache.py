"""Tests for daemon speedtest cache persistence.

These tests exercise cache load/save helpers directly to ensure the latest
speedtest snapshot survives development restarts without restoring stale data.
"""

from __future__ import annotations

import json
from pathlib import Path
import time

from casedd.config import Config
from casedd.daemon import Daemon


def _make_daemon(cache_path: Path, max_age_hours: float = 8.0) -> Daemon:
    """Build a daemon configured for speedtest cache helper tests."""
    return Daemon(
        Config(
            speedtest_cache_path=cache_path,
            speedtest_cache_max_age_hours=max_age_hours,
        )
    )


def test_load_speedtest_cache_restores_fresh_values(tmp_path: Path) -> None:
    """Fresh cache payload values are restored into the data store."""
    cache_path = tmp_path / "speedtest-cache.json"
    payload = {
        "saved_at_unix": time.time() - 30.0,
        "data": {
            "speedtest.download_mbps": 923.4,
            "speedtest.upload_mbps": 114.2,
            "speedtest.last_run": "2026-03-31 09:00:00",
            "cpu.percent": 88,
        },
    }
    cache_path.write_text(json.dumps(payload), encoding="utf-8")

    daemon = _make_daemon(cache_path)
    daemon._load_speedtest_cache()

    snapshot = daemon._store.snapshot()
    assert snapshot["speedtest.download_mbps"] == 923.4
    assert snapshot["speedtest.upload_mbps"] == 114.2
    assert snapshot["speedtest.last_run"] == "2026-03-31 09:00:00"
    assert "cpu.percent" not in snapshot


def test_load_speedtest_cache_skips_stale_values(tmp_path: Path) -> None:
    """Cache entries older than configured max-age are ignored."""
    cache_path = tmp_path / "speedtest-cache.json"
    payload = {
        "saved_at_unix": time.time() - (9.0 * 3600.0),
        "data": {
            "speedtest.download_mbps": 100.0,
            "speedtest.upload_mbps": 50.0,
        },
    }
    cache_path.write_text(json.dumps(payload), encoding="utf-8")

    daemon = _make_daemon(cache_path, max_age_hours=8.0)
    daemon._load_speedtest_cache()

    assert daemon._store.snapshot() == {}


def test_save_speedtest_cache_writes_only_speedtest_keys(tmp_path: Path) -> None:
    """Persisted cache payload contains only speedtest namespace entries."""
    cache_path = tmp_path / "speedtest-cache.json"
    daemon = _make_daemon(cache_path)
    daemon._store.update(
        {
            "speedtest.download_mbps": 800.1,
            "speedtest.upload_mbps": 100.2,
            "system.hostname": "bandit",
        }
    )

    daemon._save_speedtest_cache()

    payload_obj = json.loads(cache_path.read_text(encoding="utf-8"))
    assert isinstance(payload_obj, dict)
    assert "saved_at_unix" in payload_obj

    data_obj = payload_obj.get("data")
    assert isinstance(data_obj, dict)
    assert set(data_obj.keys()) == {
        "speedtest.download_mbps",
        "speedtest.upload_mbps",
    }
