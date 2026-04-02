"""Tests for :mod:`casedd.renderer.widgets.weather_radar`."""

from __future__ import annotations

from io import BytesIO
import logging
from urllib.error import HTTPError

from PIL import Image

from casedd.renderer.widgets.weather_radar import WeatherRadarWidget


class _FakeResponse:
    """Minimal response object for remote radar fetch tests."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def read(self) -> bytes:
        """Return the mocked response body."""
        return self._body


def test_weather_radar_widget_refreshes_after_cache_ttl(monkeypatch: object) -> None:
    """Radar images should be reused within the TTL and refreshed after it expires."""
    source = Image.new("RGB", (32, 32), (40, 120, 210))
    payload = BytesIO()
    source.save(payload, format="PNG")
    request_count = {"count": 0}
    monotonic_values = iter((100.0, 120.0, 450.0))

    def _ok(req: object, timeout: float) -> _FakeResponse:
        del req, timeout
        request_count["count"] += 1
        return _FakeResponse(payload.getvalue())

    def _monotonic() -> float:
        return next(monotonic_values)

    monkeypatch.setattr("casedd.renderer.widgets.weather_radar.urlopen", _ok)
    monkeypatch.setattr("casedd.renderer.widgets.weather_radar.time.monotonic", _monotonic)

    widget = WeatherRadarWidget()
    state: dict[str, object] = {}

    first = widget._fetch_cached_by_url(state, "https://example.invalid/radar.gif")
    second = widget._fetch_cached_by_url(state, "https://example.invalid/radar.gif")
    third = widget._fetch_cached_by_url(state, "https://example.invalid/radar.gif")

    assert first is not None
    assert second is not None
    assert third is not None
    assert request_count["count"] == 2


def test_weather_radar_widget_records_rate_limit_issue(
    monkeypatch: object,
    caplog: object,
) -> None:
    """Rate-limited radar fetches should record a badge and log the reason."""

    def _rate_limited(req: object, timeout: float) -> _FakeResponse:
        del req, timeout
        raise HTTPError("https://example.invalid/radar.gif", 429, "Too Many Requests", None, None)

    monkeypatch.setattr("casedd.renderer.widgets.weather_radar.urlopen", _rate_limited)

    widget = WeatherRadarWidget()
    state: dict[str, object] = {}

    with caplog.at_level(logging.WARNING, logger="casedd.renderer.widgets.weather_radar"):
        result = widget._fetch_cached_by_url(state, "https://example.invalid/radar.gif")

    assert result is None
    assert state["radar_fetch_badge"] == "429"
    assert "rate limited" in caplog.text


def test_weather_radar_widget_uses_metadata_indicator_when_no_station() -> None:
    """Metadata failures should surface a compact badge even without a fetch error."""
    widget = WeatherRadarWidget()

    assert widget._indicator_text({}, "unavailable", "No radar station") == "N/A"
    assert widget._indicator_text({}, "error", "NWS points lookup failed") == "META"
