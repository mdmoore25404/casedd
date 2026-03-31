"""Tests for :mod:`casedd.getters.pihole` (issue #70)."""

from __future__ import annotations

from urllib.error import HTTPError

import pytest

from casedd.data_store import DataStore
from casedd.getters.pihole import PiHoleGetter


class _FakeResponse:
    """Minimal context-managed HTTP response for urlopen monkeypatching."""

    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


async def test_pihole_getter_authenticated_success(monkeypatch) -> None:
    """Pi-hole getter should flatten key stats from authenticated responses."""

    def _ok(req, timeout: float, context=None):
        auth_header = req.get_header("Authorization")
        assert auth_header == "Bearer token-123"
        return _FakeResponse(
            """
            {
              "version": "6.0.1",
              "status": "enabled",
              "queries": {"total": 1000, "blocked": 220, "blocked_percent": 22.0},
              "clients": {"active": 12},
              "domains": {"blocked": 125000},
              "top_blocked": {"ads.example.com": 37},
              "top_clients": {"192.168.1.50": 181}
            }
            """
        )

    monkeypatch.setattr("casedd.getters.pihole.urlopen", _ok)

    getter = PiHoleGetter(
        DataStore(),
        base_url="http://pi.hole",
        api_token="token-123",
    )
    payload = await getter.fetch()

    assert payload["pihole.version"] == "6.0.1"
    assert payload["pihole.blocking.enabled"] == 1.0
    assert payload["pihole.queries.total"] == 1000.0
    assert payload["pihole.queries.blocked"] == 220.0
    assert payload["pihole.queries.blocked_percent"] == 22.0
    assert payload["pihole.clients.active_count"] == 12.0
    assert payload["pihole.domains.blocked_count"] == 125000.0
    assert payload["pihole.top_blocked.domain"] == "ads.example.com"
    assert payload["pihole.top_blocked.hits"] == 37.0
    assert payload["pihole.top_client.name"] == "192.168.1.50"
    assert payload["pihole.top_client.queries"] == 181.0


async def test_pihole_getter_blocking_disabled(monkeypatch) -> None:
    """Blocking state should map to 0.0 when Pi-hole reports disabled status."""

    def _ok(req, timeout: float, context=None):
        return _FakeResponse(
            """
            {
              "status": "disabled",
              "dns_queries_today": 99,
              "ads_blocked_today": 0,
              "ads_percentage_today": 0
            }
            """
        )

    monkeypatch.setattr("casedd.getters.pihole.urlopen", _ok)

    getter = PiHoleGetter(DataStore())
    payload = await getter.fetch()

    assert payload["pihole.blocking.enabled"] == 0.0
    assert payload["pihole.queries.total"] == 99.0
    assert payload["pihole.queries.blocked"] == 0.0
    assert payload["pihole.queries.blocked_percent"] == 0.0


async def test_pihole_getter_auth_failure(monkeypatch) -> None:
    """HTTP 401/403 auth failures should return placeholder data gracefully."""

    def _raise_auth(req, timeout: float, context=None):
        raise HTTPError(req.full_url, 401, "Unauthorized", hdrs=None, fp=None)

    monkeypatch.setattr("casedd.getters.pihole.urlopen", _raise_auth)

    getter = PiHoleGetter(DataStore(), api_token="bad")
    payload = await getter.fetch()
    # Auth failure returns placeholder dict with "-" and 0.0 values
    assert payload["pihole.version"] == "—"
    assert payload["pihole.queries.total"] == 0.0
    assert payload["pihole.top_blocked.domain"] == "—"


async def test_pihole_getter_partial_payload(monkeypatch) -> None:
    """Partial payloads should still emit defaults without hard failures."""

    def _ok(req, timeout: float, context=None):
        return _FakeResponse('{"version": "6.0.0", "queries": {"total": 10}}')

    monkeypatch.setattr("casedd.getters.pihole.urlopen", _ok)

    getter = PiHoleGetter(DataStore())
    payload = await getter.fetch()

    assert payload["pihole.version"] == "6.0.0"
    assert payload["pihole.queries.total"] == 10.0
    assert payload["pihole.queries.blocked"] == 0.0
    assert payload["pihole.clients.active_count"] == 0.0
    assert payload["pihole.top_blocked.domain"] == ""
    assert payload["pihole.top_client.name"] == ""


async def test_pihole_getter_password_auth_session(monkeypatch) -> None:
    """Getter should use password as Bearer token for authentication."""
    calls: list[tuple[str, str, str | None, str | None]] = []

    def _urlopen(req, timeout: float, context=None):
        url = str(req.full_url)
        method = str(req.get_method())
        auth_header = req.get_header("Authorization")
        calls.append((url, method, auth_header, None))

        assert url.endswith("/api/stats/summary")
        assert method == "GET"
        assert auth_header == "Bearer secret"
        return _FakeResponse(
            '{"version": "6.0.1", "queries": {"total": 5, "blocked": 1}}'
        )

    monkeypatch.setattr("casedd.getters.pihole.urlopen", _urlopen)

    getter = PiHoleGetter(DataStore(), base_url="http://pi.hole", password="secret")
    payload = await getter.fetch()

    assert payload["pihole.version"] == "6.0.1"
    assert payload["pihole.queries.total"] == 5.0
    assert payload["pihole.queries.blocked"] == 1.0
    assert calls[0] == ("http://pi.hole/api/stats/summary", "GET", "Bearer secret", None)
