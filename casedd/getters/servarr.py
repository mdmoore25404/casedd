"""Servarr-family API getters (Radarr/Sonarr).

This module provides:
- A reusable read-only Servarr API client.
- Concrete getters for Radarr and Sonarr.
- An aggregate summary getter for cross-app totals.

Store keys (per app namespace: ``radarr`` / ``sonarr``):
    - ``<ns>.active`` (1.0 when configured and reachable, else 0.0)
    - ``<ns>.queue.total``
    - ``<ns>.queue.downloading``
    - ``<ns>.queue.importing``
    - ``<ns>.queue.rows`` (rows: ``title|status / size``)
    - ``<ns>.health.warning_count``
    - ``<ns>.health.error_count``
    - ``<ns>.calendar.upcoming_count``
    - ``<ns>.disk.free_gb``
    - ``<ns>.summary``

Aggregate keys:
    - ``servarr.queue.total``
    - ``servarr.health.warning_count``
    - ``servarr.health.error_count``
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import logging
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)

_GB = 1_000_000_000


@dataclass(frozen=True)
class ServarrAppConfig:
    """Typed runtime config for one Servarr app getter."""

    app_name: str
    namespace: str
    base_url: str
    api_key: str
    interval: float
    timeout: float
    calendar_days: int
    verify_tls: bool


@dataclass(frozen=True)
class _QueueMetrics:
    """Normalized queue metrics for table/dashboard display."""

    total: float
    downloading: float
    importing: float
    rows: str


@dataclass(frozen=True)
class _HealthMetrics:
    """Normalized health-status counters."""

    warning_count: float
    error_count: float


class ServarrApiClient:
    """Minimal read-only client for Servarr-compatible APIs."""

    def __init__(
        self,
        app_name: str,
        base_url: str,
        api_key: str,
        timeout: float,
        verify_tls: bool,
    ) -> None:
        """Initialize a client for one Servarr app.

        Args:
            app_name: Friendly app name used in error messages.
            base_url: API base URL, for example ``http://localhost:7878``.
            api_key: Servarr API key value.
            timeout: HTTP timeout in seconds.
            verify_tls: Verify TLS certificates when true.
        """
        self._app_name = app_name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key.strip()
        self._timeout = timeout
        self._ssl_context: ssl.SSLContext | None = None
        if self._base_url.startswith("https://") and not verify_tls:
            self._ssl_context = ssl._create_unverified_context()  # noqa: S323

    def get_json(self, path: str, query: dict[str, object] | None = None) -> object:
        """GET one JSON endpoint and return decoded payload.

        Args:
            path: Endpoint path beginning with ``/api/v3/``.
            query: Optional query-string parameters.

        Returns:
            Parsed JSON payload (object/list/scalar).

        Raises:
            RuntimeError: For auth, transport, HTTP, or JSON failures.
        """
        query_part = ""
        if query:
            encoded = urlencode({k: str(v) for k, v in query.items()})
            if encoded:
                query_part = f"?{encoded}"

        url = f"{self._base_url}{path}{query_part}"
        headers = {
            "Accept": "application/json",
            "X-Api-Key": self._api_key,
        }
        req = Request(url, headers=headers, method="GET")  # noqa: S310

        try:
            with urlopen(  # noqa: S310
                req,
                timeout=self._timeout,
                context=self._ssl_context,
            ) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise RuntimeError(f"{self._app_name} auth failed") from exc
            if 500 <= exc.code <= 599:
                raise RuntimeError(
                    f"{self._app_name} server error (HTTP {exc.code})"
                ) from exc
            raise RuntimeError(f"{self._app_name} request failed (HTTP {exc.code})") from exc
        except URLError as exc:
            raise RuntimeError(f"{self._app_name} transport error: {exc}") from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{self._app_name} JSON parse error: {exc}") from exc


class _ServarrGetterBase(BaseGetter):
    """Shared getter behavior for one concrete Servarr app."""

    def __init__(self, store: DataStore, cfg: ServarrAppConfig) -> None:
        """Initialize one app getter.

        Args:
            store: Shared data store.
            cfg: App-specific runtime config.
        """
        super().__init__(store, cfg.interval)
        self._cfg = cfg
        self._client = ServarrApiClient(
            app_name=cfg.app_name,
            base_url=cfg.base_url,
            api_key=cfg.api_key,
            timeout=cfg.timeout,
            verify_tls=cfg.verify_tls,
        )
        self._enabled = bool(cfg.base_url.strip()) and bool(cfg.api_key.strip())
        self._inactive_logged = False

    async def fetch(self) -> dict[str, StoreValue]:
        """Fetch one normalized snapshot for the configured app."""
        return await asyncio.to_thread(self._sample)

    def _sample(self) -> dict[str, StoreValue]:
        """Run one blocking sample from Servarr APIs."""
        if not self._enabled:
            if not self._inactive_logged:
                _log.info(
                    "%s getter inactive (set base URL + API key to enable)",
                    self._cfg.app_name,
                )
                self._inactive_logged = True
            return _inactive_payload(self._cfg.namespace)

        now = datetime.now(tz=UTC)
        end = now + timedelta(days=self._cfg.calendar_days)
        queue_payload = self._client.get_json(
            "/api/v3/queue",
            {
                "page": 1,
                "pageSize": 50,
                "sortDirection": "descending",
                "sortKey": "timeleft",
            },
        )
        health_payload = self._client.get_json("/api/v3/health")
        calendar_payload = self._client.get_json(
            "/api/v3/calendar",
            {
                "start": now.date().isoformat(),
                "end": end.date().isoformat(),
            },
        )
        disk_payload = self._client.get_json("/api/v3/diskspace")

        queue = _parse_queue(queue_payload)
        health = _parse_health(health_payload)
        upcoming = _parse_upcoming_count(calendar_payload)
        free_gb = _parse_disk_free_gb(disk_payload)

        namespace = self._cfg.namespace
        return {
            f"{namespace}.active": 1.0,
            f"{namespace}.queue.total": queue.total,
            f"{namespace}.queue.downloading": queue.downloading,
            f"{namespace}.queue.importing": queue.importing,
            f"{namespace}.queue.rows": queue.rows,
            f"{namespace}.health.warning_count": health.warning_count,
            f"{namespace}.health.error_count": health.error_count,
            f"{namespace}.calendar.upcoming_count": float(upcoming),
            f"{namespace}.disk.free_gb": free_gb,
            f"{namespace}.summary": _summary(queue, health, free_gb),
        }


class RadarrGetter(_ServarrGetterBase):
    """Getter for Radarr queue/health/calendar/disk metrics."""

    def __init__(  # noqa: PLR0913
        self,
        store: DataStore,
        base_url: str,
        api_key: str,
        interval: float,
        timeout: float,
        calendar_days: int,
        verify_tls: bool,
    ) -> None:
        """Create a Radarr getter from explicit settings."""
        super().__init__(
            store,
            ServarrAppConfig(
                app_name="Radarr",
                namespace="radarr",
                base_url=base_url,
                api_key=api_key,
                interval=interval,
                timeout=timeout,
                calendar_days=calendar_days,
                verify_tls=verify_tls,
            ),
        )


class SonarrGetter(_ServarrGetterBase):
    """Getter for Sonarr queue/health/calendar/disk metrics."""

    def __init__(  # noqa: PLR0913
        self,
        store: DataStore,
        base_url: str,
        api_key: str,
        interval: float,
        timeout: float,
        calendar_days: int,
        verify_tls: bool,
    ) -> None:
        """Create a Sonarr getter from explicit settings."""
        super().__init__(
            store,
            ServarrAppConfig(
                app_name="Sonarr",
                namespace="sonarr",
                base_url=base_url,
                api_key=api_key,
                interval=interval,
                timeout=timeout,
                calendar_days=calendar_days,
                verify_tls=verify_tls,
            ),
        )


class ServarrAggregateGetter(BaseGetter):
    """Aggregate per-app Servarr keys into shared ``servarr.*`` summary keys."""

    def __init__(self, store: DataStore, interval: float = 10.0) -> None:
        """Initialize aggregate getter.

        Args:
            store: Shared data store.
            interval: Poll interval in seconds.
        """
        super().__init__(store, interval)

    async def fetch(self) -> dict[str, StoreValue]:
        """Build aggregate queue/health totals from current store snapshot."""
        snapshot = self._store.snapshot()
        total_queue = _store_float(snapshot, "radarr.queue.total") + _store_float(
            snapshot,
            "sonarr.queue.total",
        )
        total_warn = _store_float(snapshot, "radarr.health.warning_count") + _store_float(
            snapshot,
            "sonarr.health.warning_count",
        )
        total_err = _store_float(snapshot, "radarr.health.error_count") + _store_float(
            snapshot,
            "sonarr.health.error_count",
        )
        rows = "\n".join(
            [
                f"Radarr Active|{int(_store_float(snapshot, 'radarr.active'))}",
                f"Sonarr Active|{int(_store_float(snapshot, 'sonarr.active'))}",
                f"Queue Total|{int(total_queue)}",
                f"Health Warn|{int(total_warn)}",
                f"Health Error|{int(total_err)}",
            ]
        )
        return {
            "servarr.queue.total": total_queue,
            "servarr.health.warning_count": total_warn,
            "servarr.health.error_count": total_err,
            "servarr.rows": rows,
        }


def _inactive_payload(namespace: str) -> dict[str, StoreValue]:
    """Return stable placeholder keys for inactive optional apps."""
    return {
        f"{namespace}.active": 0.0,
        f"{namespace}.queue.total": 0.0,
        f"{namespace}.queue.downloading": 0.0,
        f"{namespace}.queue.importing": 0.0,
        f"{namespace}.queue.rows": "inactive|set base URL + API key",
        f"{namespace}.health.warning_count": 0.0,
        f"{namespace}.health.error_count": 0.0,
        f"{namespace}.calendar.upcoming_count": 0.0,
        f"{namespace}.disk.free_gb": 0.0,
        f"{namespace}.summary": "inactive",
    }


def _store_float(snapshot: dict[str, StoreValue], key: str) -> float:
    """Read one numeric store key with forgiving conversion."""
    raw = snapshot.get(key)
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return 0.0
        try:
            return float(text)
        except ValueError:
            return 0.0
    return 0.0


def _parse_queue(payload: object) -> _QueueMetrics:
    """Normalize queue payload from Servarr APIs."""
    records: list[dict[str, object]] = []
    total = 0

    if isinstance(payload, dict):
        total_raw = payload.get("totalRecords")
        if isinstance(total_raw, (int, float)):
            total = int(total_raw)
        records_raw = payload.get("records")
        if isinstance(records_raw, list):
            records = [entry for entry in records_raw if isinstance(entry, dict)]
    elif isinstance(payload, list):
        records = [entry for entry in payload if isinstance(entry, dict)]

    if total <= 0:
        total = len(records)

    downloading = 0
    importing = 0
    row_lines: list[str] = []

    for entry in records[:8]:
        status = str(entry.get("status", "")).strip().lower()
        if "download" in status:
            downloading += 1
        if "import" in status:
            importing += 1

        title = str(entry.get("title", "")).strip() or "(untitled)"
        size_gb = _queue_size_gb(entry)
        right = status or "unknown"
        if size_gb > 0.0:
            right = f"{right} / {size_gb:.1f}GB"
        row_lines.append(f"{title}|{right}")

    rows = "\n".join(row_lines) if row_lines else "—|—"
    return _QueueMetrics(
        total=float(total),
        downloading=float(downloading),
        importing=float(importing),
        rows=rows,
    )


def _queue_size_gb(entry: dict[str, object]) -> float:
    """Return queue item size in GB from common Servarr fields."""
    for key in ("sizeleft", "size", "remainingSize"):
        raw = entry.get(key)
        if isinstance(raw, (int, float)) and raw > 0:
            return float(raw) / _GB
    return 0.0


def _parse_health(payload: object) -> _HealthMetrics:
    """Count warning/error entries from health payload."""
    items: list[dict[str, object]] = []
    if isinstance(payload, list):
        items = [entry for entry in payload if isinstance(entry, dict)]
    elif isinstance(payload, dict):
        maybe = payload.get("records")
        if isinstance(maybe, list):
            items = [entry for entry in maybe if isinstance(entry, dict)]

    warning_count = 0
    error_count = 0
    for item in items:
        level = str(item.get("type", "")).strip().lower()
        if level == "warning":
            warning_count += 1
        elif level == "error":
            error_count += 1

    return _HealthMetrics(warning_count=float(warning_count), error_count=float(error_count))


def _parse_upcoming_count(payload: object) -> int:
    """Count upcoming calendar rows from payload."""
    if isinstance(payload, list):
        return len([entry for entry in payload if isinstance(entry, dict)])
    if isinstance(payload, dict):
        records = payload.get("records")
        if isinstance(records, list):
            return len([entry for entry in records if isinstance(entry, dict)])
    return 0


def _parse_disk_free_gb(payload: object) -> float:
    """Compute lowest free-space GB across reported root folders."""
    rows: list[dict[str, object]] = []
    if isinstance(payload, list):
        rows = [entry for entry in payload if isinstance(entry, dict)]
    elif isinstance(payload, dict):
        records = payload.get("records")
        if isinstance(records, list):
            rows = [entry for entry in records if isinstance(entry, dict)]

    free_values: list[float] = []
    for row in rows:
        free_raw = row.get("freeSpace")
        if isinstance(free_raw, (int, float)) and free_raw >= 0:
            free_values.append(float(free_raw) / _GB)

    if not free_values:
        return 0.0
    return min(free_values)


def _summary(queue: _QueueMetrics, health: _HealthMetrics, free_gb: float) -> str:
    """Build one compact status summary string."""
    return (
        f"q:{int(queue.total)} d:{int(queue.downloading)} "
        f"i:{int(queue.importing)} w:{int(health.warning_count)} "
        f"e:{int(health.error_count)} free:{free_gb:.1f}GB"
    )
