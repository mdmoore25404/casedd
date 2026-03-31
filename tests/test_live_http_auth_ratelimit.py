"""Live integration tests for update endpoint auth and rate limiting.

These tests target a running dev daemon. They are skipped unless the caller
provides the live endpoint and credentials via environment variables.
"""

from __future__ import annotations

import base64
import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

_LIVE_HTTP_URL = os.environ.get("CASEDD_LIVE_HTTP_URL", "").rstrip("/")
_LIVE_API_KEY = os.environ.get("CASEDD_LIVE_API_KEY", "")
_LIVE_BASIC_USER = os.environ.get("CASEDD_LIVE_BASIC_USER", "")
_LIVE_BASIC_PASSWORD = os.environ.get("CASEDD_LIVE_BASIC_PASSWORD", "")

if not all((_LIVE_HTTP_URL, _LIVE_API_KEY, _LIVE_BASIC_USER, _LIVE_BASIC_PASSWORD)):
    pytest.skip(
        "live HTTP auth test env vars are not configured",
        allow_module_level=True,
    )


def _basic_auth_header(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _post_update(headers: dict[str, str] | None = None) -> tuple[int, str]:
    payload = json.dumps({"update": {"live.test": 1}}).encode("utf-8")
    merged_headers = {
        "Content-Type": "application/json",
        **(headers or {}),
    }
    request = Request(
        f"{_LIVE_HTTP_URL}/api/update",
        data=payload,
        headers=merged_headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=5.0) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, body
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body


def test_live_update_missing_auth_rejected() -> None:
    status, _ = _post_update()
    assert status == 401


def test_live_update_invalid_basic_auth_rejected() -> None:
    status, _ = _post_update(_basic_auth_header(_LIVE_BASIC_USER, "wrong-password"))
    assert status == 401


def test_live_update_valid_basic_auth_accepted() -> None:
    status, _ = _post_update(_basic_auth_header(_LIVE_BASIC_USER, _LIVE_BASIC_PASSWORD))
    assert status == 204


def test_live_update_valid_api_key_accepted() -> None:
    status, _ = _post_update({"X-API-Key": _LIVE_API_KEY})
    assert status == 204


def test_live_rate_limit_blocks_after_exhaustion() -> None:
    statuses = [_post_update({"X-API-Key": _LIVE_API_KEY})[0] for _ in range(4)]
    assert 429 in statuses
    first_429 = statuses.index(429)
    assert all(status == 204 for status in statuses[:first_429])
