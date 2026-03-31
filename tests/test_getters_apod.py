"""Tests for :mod:`casedd.getters.apod`."""

from __future__ import annotations

from pathlib import Path

from casedd.data_store import DataStore
from casedd.getters.apod import ApodGetter


async def test_apod_timeout_returns_unavailable_without_cache(monkeypatch, tmp_path: Path) -> None:
    """Metadata timeout should not raise and should mark APOD unavailable."""

    def _timeout(*args, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr("casedd.getters.apod.urlopen", _timeout)

    getter = ApodGetter(DataStore(), cache_dir=str(tmp_path))
    payload = await getter.fetch()

    assert payload["apod.available"] == 0.0


async def test_apod_timeout_uses_latest_cached_image(monkeypatch, tmp_path: Path) -> None:
    """Metadata timeout should fall back to the most recent cached image."""

    def _timeout(*args, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr("casedd.getters.apod.urlopen", _timeout)

    older = tmp_path / "apod_2026-03-30.jpg"
    newer = tmp_path / "apod_2026-03-31.jpg"
    older.write_bytes(b"old")
    newer.write_bytes(b"new")
    older.touch()
    newer.touch()

    getter = ApodGetter(DataStore(), cache_dir=str(tmp_path))
    payload = await getter.fetch()

    assert payload["apod.available"] == 1.0
    assert payload["apod.image_path"] == str(newer)
