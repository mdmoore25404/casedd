"""Tests for /api/health and /api/metrics endpoints (issue #36)."""

from __future__ import annotations

from tests.conftest import _make_client


def _make_health_provider(
    status: str = "ok",
    getter_name: str = "CpuGetter",
    getter_status: str = "ok",
    uptime: float = 123.4,
    render_count: int = 42,
) -> dict[str, object]:
    """Build a minimal health snapshot dict."""
    return {
        "uptime_seconds": uptime,
        "render_count": render_count,
        "getters": [
            {
                "name": getter_name,
                "status": getter_status,
                "error_count": 0 if getter_status == "ok" else 5,
                "consecutive_errors": 0,
                "last_error_msg": None,
                "last_error_at": None,
                "last_success_at": 1000.0,
            }
        ],
    }


# ---------------------------------------------------------------------------
# /api/health
# ---------------------------------------------------------------------------


def test_health_endpoint_returns_200() -> None:
    """GET /api/health returns 200 when health_provider is set."""
    client, _ = _make_client(
        health_provider=_make_health_provider
    )
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_health_ok_status() -> None:
    """Health payload shows 'ok' when all getters are healthy."""
    client, _ = _make_client(
        health_provider=lambda: _make_health_provider(getter_status="ok")
    )
    data = client.get("/api/health").json()
    assert data["status"] == "ok"


def test_health_degraded_when_getter_errors() -> None:
    """Health status is 'degraded' when any getter has status 'error'."""
    client, _ = _make_client(
        health_provider=lambda: _make_health_provider(getter_status="error")
    )
    data = client.get("/api/health").json()
    assert data["status"] == "degraded"


def test_health_includes_uptime() -> None:
    """Health payload includes uptime_seconds field."""
    client, _ = _make_client(
        health_provider=lambda: _make_health_provider(uptime=999.9)
    )
    data = client.get("/api/health").json()
    assert data["uptime_seconds"] == pytest.approx(999.9, abs=0.1)


def test_health_includes_render_count() -> None:
    """Health payload includes render_count field."""
    client, _ = _make_client(
        health_provider=lambda: _make_health_provider(render_count=77)
    )
    data = client.get("/api/health").json()
    assert data["render_count"] == 77


def test_health_includes_getter_list() -> None:
    """Health payload includes getters list with per-getter status."""
    client, _ = _make_client(
        health_provider=lambda: _make_health_provider(getter_name="NetGetter")
    )
    data = client.get("/api/health").json()
    getters = data["getters"]
    assert isinstance(getters, list)
    assert any(g["name"] == "NetGetter" for g in getters)


def test_health_no_provider_returns_200() -> None:
    """GET /api/health works even without a health_provider (empty getters)."""
    client, _ = _make_client()
    resp = client.get("/api/health")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /api/metrics
# ---------------------------------------------------------------------------


def test_metrics_endpoint_returns_200() -> None:
    """GET /api/metrics returns 200."""
    client, _ = _make_client(
        health_provider=_make_health_provider
    )
    resp = client.get("/api/metrics")
    assert resp.status_code == 200


def test_metrics_content_type_is_text() -> None:
    """GET /api/metrics returns plain text (Prometheus format)."""
    client, _ = _make_client(
        health_provider=_make_health_provider
    )
    resp = client.get("/api/metrics")
    assert "text/plain" in resp.headers.get("content-type", "")


def test_metrics_contains_uptime_metric() -> None:
    """Metrics text contains casedd_uptime_seconds metric."""
    client, _ = _make_client(
        health_provider=_make_health_provider
    )
    body = client.get("/api/metrics").text
    assert "casedd_uptime_seconds" in body


def test_metrics_contains_render_total() -> None:
    """Metrics text contains casedd_render_total metric."""
    client, _ = _make_client(
        health_provider=_make_health_provider
    )
    body = client.get("/api/metrics").text
    assert "casedd_render_total" in body


def test_metrics_contains_getter_label() -> None:
    """Metrics text includes per-getter labels."""
    client, _ = _make_client(
        health_provider=lambda: _make_health_provider(getter_name="DiskGetter")
    )
    body = client.get("/api/metrics").text
    assert "DiskGetter" in body


def test_metrics_no_provider_still_returns_200() -> None:
    """GET /api/metrics works without a health_provider."""
    client, _ = _make_client()
    resp = client.get("/api/metrics")
    assert resp.status_code == 200


import pytest  # noqa: E402 — needed for pytest.approx above
