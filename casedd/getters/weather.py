"""Weather getter with NWS and external-provider support.

Supports two providers that emit the same ``weather.*`` keys:
- ``nws`` (official US National Weather Service APIs)
- ``open-meteo`` (external non-NWS provider example)

Location can be provided either as explicit latitude/longitude or a US zipcode.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
import json
import logging
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _LatLon:
    """Geographic coordinates."""

    lat: float
    lon: float


class WeatherGetter(BaseGetter):
    """Getter for weather and alert data from selectable providers."""

    def __init__(  # noqa: PLR0913 -- explicit config wiring is clearer
        self,
        store: DataStore,
        provider: str = "nws",
        interval: float = 300.0,
        zipcode: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
        user_agent: str = "CASEDD/0.2 (https://github.com/casedd/casedd)",
    ) -> None:
        """Initialize weather getter.

        Args:
            store: Shared data store.
            provider: ``nws`` or ``open-meteo``.
            interval: Poll interval seconds.
            zipcode: Optional US zipcode.
            lat: Optional latitude.
            lon: Optional longitude.
            user_agent: HTTP User-Agent header.
        """
        super().__init__(store, interval)
        self._provider = provider.strip().lower() or "nws"
        self._zipcode = zipcode.strip() if isinstance(zipcode, str) and zipcode.strip() else None
        self._lat = lat
        self._lon = lon
        self._user_agent = user_agent
        self._coord_cache: _LatLon | None = None

    async def fetch(self) -> dict[str, StoreValue]:
        """Collect one weather sample."""
        return await asyncio.to_thread(self._sample)

    def _sample(self) -> dict[str, StoreValue]:
        """Blocking weather sample implementation."""
        coords = self._resolve_coords()
        if coords is None:
            return {
                "weather.provider": self._provider,
                "weather.location": "unconfigured",
                "weather.conditions": "No weather location configured",
                "weather.alert_count": 0.0,
                "weather.alert_active": 0,
                "weather.radar_status": "unconfigured",
                "weather.radar_error": "No weather location configured",
            }

        if self._provider == "open-meteo":
            return self._sample_open_meteo(coords)

        return self._sample_nws(coords)

    def _resolve_coords(self) -> _LatLon | None:  # noqa: PLR0911 -- clear staged fallback checks
        """Resolve coordinates from explicit values or zipcode."""
        if self._lat is not None and self._lon is not None:
            return _LatLon(lat=self._lat, lon=self._lon)

        if self._coord_cache is not None:
            return self._coord_cache

        if self._zipcode is None:
            return None

        url = f"https://api.zippopotam.us/us/{self._zipcode}"
        payload = self._request_json(url)
        if payload is None:
            return None

        places = payload.get("places")
        if not isinstance(places, list) or not places:
            return None
        first = places[0]
        if not isinstance(first, dict):
            return None

        lat_raw = first.get("latitude")
        lon_raw = first.get("longitude")
        try:
            lat = float(lat_raw) if isinstance(lat_raw, str | int | float) else None
            lon = float(lon_raw) if isinstance(lon_raw, str | int | float) else None
        except ValueError:
            lat = None
            lon = None

        if lat is None or lon is None:
            return None

        self._coord_cache = _LatLon(lat=lat, lon=lon)
        return self._coord_cache

    def _sample_nws(  # noqa: PLR0912,PLR0915 -- network parsing is explicit by upstream payload shape
        self,
        coords: _LatLon,
    ) -> dict[str, StoreValue]:
        """Fetch weather data from NWS APIs."""
        points_url = f"https://api.weather.gov/points/{coords.lat:.4f},{coords.lon:.4f}"
        points = self._request_json(points_url)
        if points is None:
            return self._error_payload("NWS points lookup failed")

        props = cast("dict[str, object]", points.get("properties", {}))
        radar_station = str(props.get("radarStation") or "")
        forecast_url = str(props.get("forecast") or "")
        stations_url = str(props.get("observationStations") or "")
        relative_location = cast("dict[str, object]", props.get("relativeLocation", {}))
        rl_props = cast("dict[str, object]", relative_location.get("properties", {}))
        city = str(rl_props.get("city") or "")
        state = str(rl_props.get("state") or "")
        location = ", ".join(part for part in (city, state) if part)

        station_id = ""
        latest_obs: dict[str, object] | None = None
        stations = self._request_json(stations_url) if stations_url else None
        if stations is not None:
            features = stations.get("features")
            if isinstance(features, list) and features:
                first = features[0]
                if isinstance(first, dict):
                    first_props_obj = first.get("properties")
                    if isinstance(first_props_obj, dict):
                        station_id = str(first_props_obj.get("stationIdentifier") or "")
        if station_id:
            obs_url = f"https://api.weather.gov/stations/{station_id}/observations/latest"
            latest_obs = self._request_json(obs_url)

        temp_f = 0.0
        humidity = 0.0
        wind_mph = 0.0
        condition = "Unknown"
        icon_url = ""
        if latest_obs is not None:
            obs_props = cast("dict[str, object]", latest_obs.get("properties", {}))
            condition = str(obs_props.get("textDescription") or "Unknown")
            icon_url = str(obs_props.get("icon") or "")
            temp_c_obj = cast("dict[str, object]", obs_props.get("temperature", {}))
            humidity_obj = cast("dict[str, object]", obs_props.get("relativeHumidity", {}))
            wind_obj = cast("dict[str, object]", obs_props.get("windSpeed", {}))
            temp_c = _safe_float(temp_c_obj.get("value"))
            humidity = _safe_float(humidity_obj.get("value")) or 0.0
            wind_kmh = _safe_float(wind_obj.get("value"))
            temp_f = ((temp_c * 9.0) / 5.0) + 32.0 if temp_c is not None else 0.0
            wind_mph = wind_kmh * 0.621371 if wind_kmh is not None else 0.0

        short_forecast = ""
        forecast_table = ""
        if forecast_url:
            forecast = self._request_json(forecast_url)
            if forecast is not None:
                forecast_props_obj = forecast.get("properties")
                forecast_props = (
                    cast("dict[str, object]", forecast_props_obj)
                    if isinstance(forecast_props_obj, dict)
                    else {}
                )
                periods = forecast_props.get("periods")
                if isinstance(periods, list) and periods:
                    first_period = periods[0]
                    if isinstance(first_period, dict):
                        short_forecast = str(first_period.get("shortForecast") or "")
                    forecast_table = _build_nws_forecast_table(periods)

        alert_url = (
            "https://api.weather.gov/alerts/active?"
            + urlencode({"point": f"{coords.lat:.4f},{coords.lon:.4f}"})
        )
        alerts = self._request_json(alert_url)
        alert_count = 0
        alert_summary = "None"
        alert_level = "none"
        if alerts is not None:
            features = alerts.get("features")
            if isinstance(features, list):
                alert_count = len(features)
                if features:
                    headlines: list[str] = []
                    top_rank = 0
                    for feature in features[:3]:
                        if not isinstance(feature, dict):
                            continue
                        props_alert = feature.get("properties")
                        if not isinstance(props_alert, dict):
                            continue
                        event = str(props_alert.get("event") or "Alert")
                        severity = str(props_alert.get("severity") or "")
                        headlines.append(f"{event} {severity}".strip())
                        top_rank = max(top_rank, _alert_rank(event, severity))
                    if headlines:
                        alert_summary = " | ".join(headlines)
                    alert_level = _rank_to_alert_level(top_rank)

        radar_image_url = ""
        radar_url = ""
        radar_status = "ok"
        radar_error = ""
        if radar_station:
            radar_url = f"https://radar.weather.gov/station/{radar_station}"
            radar_image_url = (
                "https://radar.weather.gov/ridge/standard/"
                f"{radar_station}_loop.gif"
            )
        else:
            radar_status = "unavailable"
            radar_error = "No NWS radar station for the selected location"

        return {
            "weather.provider": "nws",
            "weather.location": location or f"{coords.lat:.4f},{coords.lon:.4f}",
            "weather.conditions": condition,
            "weather.temp_f": round(max(-99.0, temp_f), 1),
            "weather.wind_mph": round(max(0.0, wind_mph), 1),
            "weather.humidity_percent": round(max(0.0, humidity), 1),
            "weather.icon_url": icon_url,
            "weather.forecast_short": short_forecast,
            "weather.forecast_table": forecast_table,
            "weather.alert_count": float(alert_count),
            "weather.alert_active": 1 if alert_count > 0 else 0,
            "weather.alert_level": alert_level,
            "weather.alert_summary": alert_summary,
            "weather.watch_warning": alert_summary,
            "weather.radar_station": radar_station,
            "weather.radar_url": radar_url,
            "weather.radar_image_url": radar_image_url,
            "weather.radar_status": radar_status,
            "weather.radar_error": radar_error,
        }

    def _sample_open_meteo(self, coords: _LatLon) -> dict[str, StoreValue]:
        """Fetch weather data from Open-Meteo as external-provider example."""
        url = (
            "https://api.open-meteo.com/v1/forecast?"
            + urlencode(
                {
                    "latitude": f"{coords.lat:.4f}",
                    "longitude": f"{coords.lon:.4f}",
                    "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
                    "daily": (
                        "temperature_2m_max,temperature_2m_min,"
                        "precipitation_probability_max,wind_speed_10m_max"
                    ),
                    "forecast_days": 5,
                    "temperature_unit": "fahrenheit",
                    "wind_speed_unit": "mph",
                    "timezone": "auto",
                }
            )
        )
        payload = self._request_json(url)
        if payload is None:
            return self._error_payload("Open-Meteo request failed")

        current = payload.get("current")
        if not isinstance(current, dict):
            return self._error_payload("Open-Meteo payload missing current")

        temp_f = _safe_float(current.get("temperature_2m")) or 0.0
        humidity = _safe_float(current.get("relative_humidity_2m")) or 0.0
        wind_mph = _safe_float(current.get("wind_speed_10m")) or 0.0
        code = int(_safe_float(current.get("weather_code")) or 0)
        condition = _open_meteo_code_to_text(code)
        forecast_table = _build_open_meteo_forecast_table(payload)

        return {
            "weather.provider": "open-meteo",
            "weather.location": f"{coords.lat:.4f},{coords.lon:.4f}",
            "weather.conditions": condition,
            "weather.temp_f": round(temp_f, 1),
            "weather.wind_mph": round(max(0.0, wind_mph), 1),
            "weather.humidity_percent": round(max(0.0, humidity), 1),
            "weather.icon_url": "",
            "weather.forecast_short": condition,
            "weather.forecast_table": forecast_table,
            "weather.alert_count": 0.0,
            "weather.alert_active": 0,
            "weather.alert_level": "none",
            "weather.alert_summary": "No external alert feed configured",
            "weather.watch_warning": "No external alert feed configured",
            "weather.radar_station": "",
            "weather.radar_url": "https://open-meteo.com/en/docs",
            "weather.radar_image_url": "",
            "weather.radar_status": "unsupported",
            "weather.radar_error": "Open-Meteo does not provide a CASEDD radar image feed",
        }

    def _request_json(self, url: str) -> dict[str, object] | None:
        """Fetch one JSON document with basic headers."""
        req = Request(  # noqa: S310 -- URL is controlled by provider selection
            url,
            headers={
                "User-Agent": self._user_agent,
                "Accept": "application/geo+json, application/json",
            },
            method="GET",
        )
        try:
            with urlopen(req, timeout=8) as resp:  # noqa: S310 -- controlled URL
                raw = resp.read().decode("utf-8")
        except HTTPError as exc:
            if exc.code == 429:
                _log.warning("weather request rate limited (%s): %s", exc.code, url)
            else:
                _log.warning("weather request HTTP %s: %s", exc.code, url)
            return None
        except URLError:
            _log.warning("weather request failed: %s", url, exc_info=True)
            return None

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            _log.warning("weather JSON decode failed: %s", url, exc_info=True)
            return None

        return payload if isinstance(payload, dict) else None

    def _error_payload(self, message: str) -> dict[str, StoreValue]:
        """Return a normalized error payload for weather widgets."""
        return {
            "weather.provider": self._provider,
            "weather.location": "unavailable",
            "weather.conditions": message,
            "weather.alert_count": 0.0,
            "weather.alert_active": 0,
            "weather.alert_level": "none",
            "weather.forecast_table": "",
            "weather.alert_summary": "Unavailable",
            "weather.watch_warning": "Unavailable",
            "weather.radar_url": "",
            "weather.radar_image_url": "",
            "weather.radar_status": "error",
            "weather.radar_error": message,
        }


def _safe_float(raw: object) -> float | None:
    """Convert basic scalar input to float."""
    if isinstance(raw, int | float):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def _open_meteo_code_to_text(code: int) -> str:
    """Map Open-Meteo weather code to readable condition text."""
    mapping: dict[int, str] = {
        0: "Clear",
        1: "Mostly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Fog",
        48: "Rime fog",
        51: "Light drizzle",
        53: "Drizzle",
        55: "Heavy drizzle",
        61: "Light rain",
        63: "Rain",
        65: "Heavy rain",
        71: "Light snow",
        73: "Snow",
        75: "Heavy snow",
        80: "Rain showers",
        81: "Moderate showers",
        82: "Violent showers",
        95: "Thunderstorm",
        96: "Thunderstorm hail",
        99: "Severe thunderstorm hail",
    }
    return mapping.get(code, f"Code {code}")


def _build_nws_forecast_table(periods: list[object]) -> str:
    """Build a compact day/low-high/precip/wind forecast table from NWS periods."""
    if not periods:
        return ""

    by_day: dict[str, dict[str, float | str | None]] = {}
    day_order: list[str] = []

    for period_obj in periods:
        if not isinstance(period_obj, dict):
            continue
        day_key = _day_from_period(period_obj)
        if day_key is None:
            continue
        if day_key not in by_day:
            by_day[day_key] = {"hi": None, "lo": None, "pop": None, "wind": "--", "condition": ""}
            day_order.append(day_key)

        _merge_nws_period(by_day[day_key], period_obj)

    if not day_order:
        return ""

    rows: list[str] = []
    for day_key in day_order[:5]:
        bucket = by_day[day_key]
        hi = bucket["hi"]
        lo = bucket["lo"]
        pop = bucket["pop"]
        wind = str(bucket["wind"] or "--")
        condition = str(bucket.get("condition") or "")
        lo_raw = round(lo) if isinstance(lo, float) else ""
        hi_raw = round(hi) if isinstance(hi, float) else ""
        pop_raw = round(pop) if isinstance(pop, float) else ""
        rows.append(f"{day_key}|{lo_raw}|{hi_raw}|{pop_raw}|{wind}|{condition}")

    return "\n".join(rows)


def _build_open_meteo_forecast_table(payload: dict[str, object]) -> str:
    """Build forecast rows from Open-Meteo daily arrays."""
    daily_obj = payload.get("daily")
    if not isinstance(daily_obj, dict):
        return ""

    times = daily_obj.get("time")
    hi_vals = daily_obj.get("temperature_2m_max")
    lo_vals = daily_obj.get("temperature_2m_min")
    pop_vals = daily_obj.get("precipitation_probability_max")
    wind_vals = daily_obj.get("wind_speed_10m_max")

    if not isinstance(times, list):
        return ""

    rows: list[str] = []
    count = min(5, len(times))
    for idx in range(count):
        day = _short_day(str(times[idx]))
        hi = _to_float_at(hi_vals, idx)
        lo = _to_float_at(lo_vals, idx)
        pop = _to_float_at(pop_vals, idx)
        wind = _to_float_at(wind_vals, idx)
        lo_raw = round(lo) if lo is not None else ""
        hi_raw = round(hi) if hi is not None else ""
        pop_raw = round(pop) if pop is not None else ""
        wind_text = f"{round(wind)}mph" if wind is not None else "--"
        rows.append(f"{day}|{lo_raw}|{hi_raw}|{pop_raw}|{wind_text}|")

    return "\n".join(rows)


def _short_day(raw_date: str) -> str:
    """Convert YYYY-MM-DD or ISO date strings to 3-letter uppercase day name."""
    try:
        dt = date.fromisoformat(raw_date[:10])
    except ValueError:
        return "DAY"
    return dt.strftime("%a").upper()[:3]


def _to_float_at(values: object, idx: int) -> float | None:
    """Read one numeric element from an array-like object."""
    if not isinstance(values, list):
        return None
    if idx < 0 or idx >= len(values):
        return None
    return _safe_float(values[idx])


def _fmt_temp(value: float | str | None) -> str:
    """Format temperature tokens for compact forecast rows."""
    if isinstance(value, float):
        return f"{round(value):>2}"
    return "--"


def _day_from_period(period_obj: dict[str, object]) -> str | None:
    """Extract 3-letter day token from an NWS period object."""
    start_raw = str(period_obj.get("startTime") or "")
    dt = _parse_iso_datetime(start_raw)
    if dt is None:
        return None
    return dt.strftime("%a").upper()[:3]


def _merge_nws_period(
    bucket: dict[str, float | str | None],
    period_obj: dict[str, object],
) -> None:
    """Merge one NWS period into accumulated day bucket fields."""
    temp = _safe_float(period_obj.get("temperature"))
    is_daytime = bool(period_obj.get("isDaytime"))
    if temp is not None:
        if is_daytime:
            bucket["hi"] = temp
        elif bucket["lo"] is None:
            bucket["lo"] = temp

    pop_obj = period_obj.get("probabilityOfPrecipitation")
    if isinstance(pop_obj, dict):
        pop = _safe_float(pop_obj.get("value"))
        if pop is not None:
            current_pop = bucket["pop"]
            if not isinstance(current_pop, float) or pop > current_pop:
                bucket["pop"] = pop

    if not is_daytime:
        return

    wind_speed = _extract_first_float(str(period_obj.get("windSpeed") or ""))
    wind_dir = str(period_obj.get("windDirection") or "").strip().upper()
    if wind_speed is not None:
        speed_text = f"{round(wind_speed)}mph"
        bucket["wind"] = f"{speed_text} {wind_dir}".strip()

    short_forecast = str(period_obj.get("shortForecast") or "")
    if short_forecast and not bucket.get("condition"):
        bucket["condition"] = short_forecast


def _parse_iso_datetime(raw: str) -> datetime | None:
    """Parse ISO datetime string, including trailing Z form."""
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _extract_first_float(raw: str) -> float | None:
    """Extract first numeric token from strings like '5 to 10 mph'."""
    if not raw:
        return None
    for token in raw.replace("-", " ").split():
        number = _safe_float(token)
        if number is not None:
            return number
    return None


def _alert_rank(event: str, severity: str) -> int:
    """Map alert event/severity text to ranking for color policy."""
    sev = severity.strip().lower()
    evt = event.strip().lower()
    if "warning" in evt or sev in {"extreme", "severe"}:
        return 3
    if "watch" in evt or sev == "moderate":
        return 2
    if "advisory" in evt or sev in {"minor", "unknown"}:
        return 1
    return 1 if evt else 0


def _rank_to_alert_level(rank: int) -> str:
    """Convert alert rank to canonical level string."""
    if rank >= 3:
        return "warning"
    if rank == 2:
        return "watch"
    if rank == 1:
        return "advisory"
    return "none"
