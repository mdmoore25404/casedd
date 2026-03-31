"""Tests for rate limiting and API-key auth on update endpoints (issue #29)."""

from __future__ import annotations

from casedd.outputs.http_viewer import _RateLimiter
from tests.conftest import _make_client

_UPDATE_BODY = {"update": {"test.val": 42}}


# ---------------------------------------------------------------------------
# _RateLimiter unit tests
# ---------------------------------------------------------------------------


def test_rate_limiter_disabled_when_zero() -> None:
    """A limit of 0 always allows requests."""
    lim = _RateLimiter(0)
    for _ in range(1000):
        assert lim.is_allowed("192.168.1.1") is True


def test_rate_limiter_allows_up_to_limit() -> None:
    """Requests up to max_per_minute are allowed."""
    lim = _RateLimiter(3)
    for _ in range(3):
        assert lim.is_allowed("10.0.0.1") is True


def test_rate_limiter_blocks_over_limit() -> None:
    """The (limit+1)-th request from the same IP is blocked."""
    lim = _RateLimiter(3)
    for _ in range(3):
        lim.is_allowed("10.0.0.2")
    assert lim.is_allowed("10.0.0.2") is False


def test_rate_limiter_tracks_ips_independently() -> None:
    """Different IPs each get their own independent quota."""
    lim = _RateLimiter(2)
    lim.is_allowed("1.1.1.1")
    lim.is_allowed("1.1.1.1")
    # 1.1.1.1 is exhausted, 2.2.2.2 still has quota
    assert lim.is_allowed("1.1.1.1") is False
    assert lim.is_allowed("2.2.2.2") is True


# ---------------------------------------------------------------------------
# Auth — no key configured
# ---------------------------------------------------------------------------


def test_update_without_key_configured_succeeds() -> None:
    """When no api_key is set, update endpoint accepts any request."""
    client, _ = _make_client()
    resp = client.post("/api/update", json=_UPDATE_BODY)
    assert resp.status_code == 204


def test_update_with_extra_header_still_succeeds_when_no_key() -> None:
    """Passing X-API-Key when none is configured is harmless."""
    client, _ = _make_client()
    resp = client.post(
        "/api/update",
        json=_UPDATE_BODY,
        headers={"X-API-Key": "anything"},
    )
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Auth — key configured
# ---------------------------------------------------------------------------


def test_update_missing_key_returns_401() -> None:
    """Missing X-API-Key header returns 401 when key is configured."""
    client, _ = _make_client(api_key="secret")
    resp = client.post("/api/update", json=_UPDATE_BODY)
    assert resp.status_code == 401


def test_update_wrong_key_returns_401() -> None:
    """Wrong X-API-Key returns 401."""
    client, _ = _make_client(api_key="secret")
    resp = client.post(
        "/api/update",
        json=_UPDATE_BODY,
        headers={"X-API-Key": "wrong"},
    )
    assert resp.status_code == 401


def test_update_correct_key_returns_204() -> None:
    """Correct X-API-Key returns 204."""
    client, _ = _make_client(api_key="secret")
    resp = client.post(
        "/api/update",
        json=_UPDATE_BODY,
        headers={"X-API-Key": "secret"},
    )
    assert resp.status_code == 204


def test_legacy_update_also_enforces_auth() -> None:
    """The legacy POST /update endpoint also checks the API key."""
    client, _ = _make_client(api_key="secret")
    resp = client.post("/update", json=_UPDATE_BODY, headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Rate limiting (integration)
# ---------------------------------------------------------------------------


def test_rate_limit_blocks_after_exhaustion() -> None:
    """After exceeding the rate limit, the endpoint returns 429."""
    client, _ = _make_client(api_rate_limit=3)
    codes = []
    for _ in range(5):
        resp = client.post("/api/update", json=_UPDATE_BODY)
        codes.append(resp.status_code)
    # First 3 should succeed, remainder should be 429
    assert codes[:3] == [204, 204, 204]
    assert all(c == 429 for c in codes[3:])


def test_rate_limit_with_correct_auth() -> None:
    """Rate limiting applies even when the API key is correct."""
    client, _ = _make_client(api_key="k", api_rate_limit=2)
    headers = {"X-API-Key": "k"}
    # 2 allowed
    assert client.post("/api/update", json=_UPDATE_BODY, headers=headers).status_code == 204
    assert client.post("/api/update", json=_UPDATE_BODY, headers=headers).status_code == 204
    # 3rd blocked
    assert client.post("/api/update", json=_UPDATE_BODY, headers=headers).status_code == 429
