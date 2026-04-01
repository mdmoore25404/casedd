"""Tests for Servarr getters (Radarr/Sonarr)."""

from __future__ import annotations

import asyncio
import json
from urllib.error import HTTPError

import pytest

from casedd.data_store import DataStore
from casedd.getters.servarr import RadarrGetter, ServarrAggregateGetter, SonarrGetter


class _FakeResponse:
    """Minimal context-managed HTTP response for urlopen monkeypatching."""

    def __init__(self, body: object) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class _ServarrUrlOpen:
    """Return deterministic payloads for Radarr and Sonarr endpoints."""

    def __call__(self, req, timeout: float, context=None):
        _ = timeout
        _ = context
        url = str(req.full_url)
        endpoint = ""
        for candidate in (
            "/api/v3/queue",
            "/api/v3/health",
            "/api/v3/calendar",
            "/api/v3/diskspace",
        ):
            if candidate in url:
                endpoint = candidate
                break

        if not endpoint:
            raise AssertionError(f"Unhandled URL: {url}")

        payload = self._payload_for(url, endpoint)
        if payload is None:
            raise AssertionError(f"Unhandled URL: {url}")
        return _FakeResponse(payload)

    def _payload_for(self, url: str, endpoint: str) -> object | None:
        """Return response payload for a known URL/endpoint pair."""
        if "radarr.local" in url:
            return self._radarr_payload(endpoint)
        if "sonarr.local" in url:
            return self._sonarr_payload(endpoint)
        return None

    def _radarr_payload(self, endpoint: str) -> object | None:
        """Return Radarr fixture payload for one endpoint."""
        if endpoint == "/api/v3/queue":
            return {
                "totalRecords": 4,
                "records": [
                    {
                        "title": "Movie A",
                        "status": "downloading",
                        "sizeleft": 8_000_000_000,
                    },
                    {
                        "title": "Movie B",
                        "status": "importPending",
                        "sizeleft": 2_000_000_000,
                    },
                    {"title": "Movie C", "status": "queued", "sizeleft": 1_000_000_000},
                    {
                        "title": "Movie D",
                        "status": "downloading",
                        "sizeleft": 500_000_000,
                    },
                ],
            }
        if endpoint == "/api/v3/health":
            return [
                {"type": "warning", "message": "Indexer lagging"},
                {"type": "error", "message": "Disk full soon"},
                {"type": "warning", "message": "Delay profile mismatch"},
            ]
        if endpoint == "/api/v3/calendar":
            return [{"title": "Movie A"}, {"title": "Movie B"}]
        if endpoint == "/api/v3/diskspace":
            return [
                {"path": "/data", "freeSpace": 90_000_000_000},
                {"path": "/downloads", "freeSpace": 15_000_000_000},
            ]
        return None

    def _sonarr_payload(self, endpoint: str) -> object | None:
        """Return Sonarr fixture payload for one endpoint."""
        if endpoint == "/api/v3/queue":
            return {
                "totalRecords": 1,
                "records": [{"title": "Show X", "status": "queued"}],
            }
        if endpoint == "/api/v3/health":
            return []
        if endpoint == "/api/v3/calendar":
            return []
        if endpoint == "/api/v3/diskspace":
            return [{"freeSpace": 300_000_000_000}]
        return None


class _AuthFailUrlOpen:
    """Raise a 401 for any request."""

    def __call__(self, req, timeout: float, context=None):
        _ = timeout
        _ = context
        raise HTTPError(
            url=str(req.full_url),
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=None,
        )


async def test_radarr_getter_normalizes_queue_health_calendar_and_disk(monkeypatch) -> None:
    """Radarr getter should normalize queue counts, health levels, and free-space floor."""
    monkeypatch.setattr("casedd.getters.servarr.urlopen", _ServarrUrlOpen())

    getter = RadarrGetter(
        DataStore(),
        base_url="http://radarr.local:7878",
        api_key="radarr-key",
        interval=5.0,
        timeout=2.0,
        calendar_days=7,
        verify_tls=True,
    )

    payload = await getter.fetch()

    assert payload["radarr.active"] == 1.0
    assert payload["radarr.queue.total"] == 4.0
    assert payload["radarr.queue.downloading"] == 2.0
    assert payload["radarr.queue.importing"] == 1.0
    assert payload["radarr.health.warning_count"] == 2.0
    assert payload["radarr.health.error_count"] == 1.0
    assert payload["radarr.calendar.upcoming_count"] == 2.0
    assert payload["radarr.disk.free_gb"] == 15.0
    assert "Movie A|downloading" in str(payload["radarr.queue.rows"])


async def test_servarr_auth_failure_raises_runtime_error(monkeypatch) -> None:
    """401/403 responses should be surfaced as auth failures for health tracking."""
    monkeypatch.setattr("casedd.getters.servarr.urlopen", _AuthFailUrlOpen())

    getter = SonarrGetter(
        DataStore(),
        base_url="http://sonarr.local:8989",
        api_key="bad-key",
        interval=5.0,
        timeout=2.0,
        calendar_days=7,
        verify_tls=True,
    )

    with pytest.raises(RuntimeError, match="auth failed"):
        await getter.fetch()


async def test_partial_app_availability_keeps_missing_app_inactive(monkeypatch) -> None:
    """Configured app should return data while unconfigured app stays inactive."""
    monkeypatch.setattr("casedd.getters.servarr.urlopen", _ServarrUrlOpen())

    radarr = RadarrGetter(
        DataStore(),
        base_url="http://radarr.local:7878",
        api_key="radarr-key",
        interval=5.0,
        timeout=2.0,
        calendar_days=7,
        verify_tls=True,
    )
    sonarr = SonarrGetter(
        DataStore(),
        base_url="",
        api_key="",
        interval=5.0,
        timeout=2.0,
        calendar_days=7,
        verify_tls=True,
    )

    radarr_payload = await radarr.fetch()
    sonarr_payload = await sonarr.fetch()

    assert radarr_payload["radarr.active"] == 1.0
    assert sonarr_payload["sonarr.active"] == 0.0
    assert sonarr_payload["sonarr.summary"] == "inactive"


async def test_servarr_aggregate_getter_sums_app_totals() -> None:
    """Aggregate getter should sum queue and health counters from both apps."""
    store = DataStore()
    store.update(
        {
            "radarr.queue.total": 5.0,
            "sonarr.queue.total": 3.0,
            "radarr.health.warning_count": 2.0,
            "sonarr.health.warning_count": 1.0,
            "radarr.health.error_count": 1.0,
            "sonarr.health.error_count": 0.0,
        }
    )

    getter = ServarrAggregateGetter(store, interval=5.0)
    payload = await getter.fetch()

    assert payload["servarr.queue.total"] == 8.0
    assert payload["servarr.health.warning_count"] == 3.0
    assert payload["servarr.health.error_count"] == 1.0
    radarr_rows = str(payload["servarr.radarr.rows"])
    sonarr_rows = str(payload["servarr.sonarr.rows"])
    totals_rows = str(payload["servarr.totals.rows"])
    assert "Queue|5" in radarr_rows
    assert "Queue|3" in sonarr_rows
    assert "Queue Total|8" in totals_rows
    assert str(payload["servarr.rows"]) == totals_rows


def test_servarr_aggregate_getter_handles_string_values() -> None:
    """Aggregate getter should coerce numeric strings to floats safely."""

    async def _run() -> dict[str, float | int | str]:
        store = DataStore()
        store.update(
            {
                "radarr.queue.total": "4",
                "sonarr.queue.total": 2.0,
                "radarr.health.warning_count": "bad",
                "sonarr.health.warning_count": "1",
                "radarr.health.error_count": 0.0,
                "sonarr.health.error_count": "2",
            }
        )
        getter = ServarrAggregateGetter(store, interval=5.0)
        return await getter.fetch()

    payload = asyncio.run(_run())
    assert payload["servarr.queue.total"] == 6.0
    assert payload["servarr.health.warning_count"] == 1.0
    assert payload["servarr.health.error_count"] == 2.0
    assert "Free|0.0GB" in str(payload["servarr.radarr.rows"])
    assert "Free|0.0GB" in str(payload["servarr.sonarr.rows"])
