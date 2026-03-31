"""Pi-hole API getter.

Polls Pi-hole REST endpoints and publishes flattened ``pihole.*`` keys for
network/privacy dashboard widgets.

Store keys written:
    - ``pihole.version``
    - ``pihole.blocking.enabled`` (1.0 enabled, 0.0 disabled)
    - ``pihole.queries.total``
    - ``pihole.queries.blocked``
    - ``pihole.queries.blocked_percent``
    - ``pihole.clients.active_count``
    - ``pihole.domains.blocked_count``
    - ``pihole.top_blocked.domain``
    - ``pihole.top_blocked.hits``
    - ``pihole.top_blocked.list`` (rows: ``name|count``)
    - ``pihole.top_client.name``
    - ``pihole.top_client.queries``
    - ``pihole.top_client.list`` (rows: ``name|count``)
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)


class PiHoleGetter(BaseGetter):
    """Getter for Pi-hole DNS filtering and query telemetry.

    Args:
        store: Shared data store.
        base_url: Pi-hole API base URL.
        api_token: Optional API token for bearer authentication (legacy token).
        password: Optional Pi-hole app password for bearer authentication (v6+).
        session_sid: Optional API session cookie sid value.
        interval: Poll interval in seconds.
        timeout: HTTP timeout in seconds.
        verify_tls: Verify TLS certificates for HTTPS endpoints.
    """

    def __init__(  # noqa: PLR0913 -- explicit config wiring is clearer
        self,
        store: DataStore,
        base_url: str = "http://pi.hole",
        api_token: str | None = None,
        password: str | None = None,
        session_sid: str | None = None,
        interval: float = 5.0,
        timeout: float = 4.0,
        verify_tls: bool = True,
    ) -> None:
        """Initialise Pi-hole getter settings."""
        super().__init__(store, interval)
        self._base_url = base_url.rstrip("/")
        self._api_token = api_token.strip() if isinstance(api_token, str) else ""
        self._password = password.strip() if isinstance(password, str) else ""
        self._session_sid = session_sid.strip() if isinstance(session_sid, str) else ""
        self._password_session_sid = ""
        self._timeout = timeout
        self._ssl_context: ssl.SSLContext | None = None
        self._auth_error_logged = False
        if self._base_url.startswith("https://") and not verify_tls:
            self._ssl_context = ssl._create_unverified_context()  # noqa: S323

    async def fetch(self) -> dict[str, StoreValue]:
        """Collect one Pi-hole sample and normalize to flattened keys."""
        return await asyncio.to_thread(self._sample)

    def _sample(self) -> dict[str, StoreValue]:
        """Blocking Pi-hole poll implementation."""
        try:
            payload = self._request_json("/api/stats/summary")
        except RuntimeError as exc:
            if "auth failed" in str(exc).lower():
                if not self._auth_error_logged:
                    _log.error(
                        "Pi-hole auth failed. Please verify credentials:\n"
                        "  1. Log into http://%s (or configure CASEDD_PIHOLE_BASE_URL)\n"
                        "  2. Settings → API/Web Interface → Create/Copy API Token\n"
                        "  3. Update CASEDD_PIHOLE_PASSWORD in .env with the token\n"
                        "  4. Restart with: ./dev.sh restart\n"
                        "Error: %s",
                        self._base_url.split("://")[-1].split(":")[0] or "pi.hole",
                        exc,
                    )
                    self._auth_error_logged = True
                return self._placeholder_sample()
            raise

        version_payload = self._request_json_optional("/api/info/version")
        top_blocked_payload = self._request_json_optional(
            "/api/stats/top_domains?blocked=true"
        )
        top_clients_payload = self._request_json_optional("/api/stats/top_clients")

        total_queries = _first_number(
            payload,
            [
                ("queries", "total"),
                ("queries_total",),
                ("dns_queries_today",),
                ("total_queries",),
            ],
        )
        blocked_queries = _first_number(
            payload,
            [
                ("queries", "blocked"),
                ("queries_blocked",),
                ("ads_blocked_today",),
                ("blocked_queries",),
            ],
        )
        blocked_percent = _first_number(
            payload,
            [
                ("queries", "blocked_percent"),
                ("queries", "percent_blocked"),
                ("blocked_percent",),
                ("ads_percentage_today",),
                ("percentage_blocked",),
            ],
        )

        if blocked_percent <= 0.0 and total_queries > 0.0:
            blocked_percent = round((blocked_queries / total_queries) * 100.0, 2)

        blocking_enabled = _extract_blocking_enabled(payload)
        active_clients = _first_number(
            payload,
            [
                ("clients", "active"),
                ("active_clients",),
                ("unique_clients",),
            ],
        )
        blocked_domains = _first_number(
            payload,
            [
                ("domains", "blocked"),
                ("domains_being_blocked",),
                ("gravity", "domains_being_blocked"),
            ],
        )
        top_blocked_domain, top_blocked_hits = _extract_top_entry(
            top_blocked_payload,
            keys=(("domains",), ("top_blocked",), ("top", "blocked"), ("top_ads",)),
            name_fields=("domain", "name", "item"),
            value_fields=("count", "hits", "queries", "value"),
        )
        top_blocked_rows = _extract_top_entries(
            top_blocked_payload,
            keys=(("domains",), ("top_blocked",), ("top", "blocked"), ("top_ads",)),
            name_fields=("domain", "name", "item"),
            value_fields=("count", "hits", "queries", "value"),
            limit=5,
        )
        top_client_name, top_client_queries = _extract_top_entry(
            top_clients_payload,
            keys=(("clients",), ("top_clients",), ("top", "clients")),
            name_fields=("client", "name", "ip", "item"),
            value_fields=("count", "queries", "hits", "value"),
        )
        top_client_rows = _extract_top_entries(
            top_clients_payload,
            keys=(("clients",), ("top_clients",), ("top", "clients")),
            name_fields=("client", "name", "ip", "item"),
            value_fields=("count", "queries", "hits", "value"),
            limit=5,
        )

        return {
            "pihole.version": _first_text(
                version_payload,
                [
                    ("version", "ftl", "local", "version"),
                    ("version", "core", "local", "version"),
                    ("version", "web", "local", "version"),
                    ("version",),
                    ("pihole_version",),
                    ("meta", "version"),
                ],
            )
            or _first_text(
                payload,
                [
                    ("version",),
                    ("pihole_version",),
                    ("meta", "version"),
                ],
            ),
            "pihole.blocking.enabled": 1.0 if blocking_enabled else 0.0,
            "pihole.queries.total": float(total_queries),
            "pihole.queries.blocked": float(blocked_queries),
            "pihole.queries.blocked_percent": float(blocked_percent),
            "pihole.clients.active_count": float(active_clients),
            "pihole.domains.blocked_count": float(blocked_domains),
            "pihole.top_blocked.domain": top_blocked_domain,
            "pihole.top_blocked.hits": float(top_blocked_hits),
            "pihole.top_blocked.list": _format_ranked_list(top_blocked_rows),
            "pihole.top_client.name": top_client_name,
            "pihole.top_client.queries": float(top_client_queries),
            "pihole.top_client.list": _format_ranked_list(top_client_rows),
        }

    def _request_json(self, path: str, *, retry_auth: bool = True) -> dict[str, object]:
        """GET one Pi-hole endpoint and parse JSON payload."""
        url = f"{self._base_url}{path}"
        headers = {"Accept": "application/json"}
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"
        elif self._password:
            self._ensure_password_session_sid()

        sid = self._active_session_sid()
        if sid:
            headers["X-FTL-SID"] = sid
            headers["Cookie"] = f"sid={sid}"

        req = Request(url, headers=headers, method="GET")  # noqa: S310 -- user-provided API endpoint
        try:
            with urlopen(  # noqa: S310 -- user-provided API endpoint
                req,
                timeout=self._timeout,
                context=self._ssl_context,
            ) as resp:
                body = resp.read().decode("utf-8")
        except HTTPError as exc:
            if exc.code in {401, 403}:
                if retry_auth and self._can_refresh_password_session():
                    self._password_session_sid = ""
                    self._ensure_password_session_sid()
                    return self._request_json(path, retry_auth=False)
                msg = "Pi-hole auth failed (check token/session credentials)"
                raise RuntimeError(msg) from exc
            raise RuntimeError(f"Pi-hole request failed with HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"Pi-hole transport error: {exc}") from exc

        try:
            decoded = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Pi-hole JSON parse error: {exc}") from exc

        if not isinstance(decoded, dict):
            raise RuntimeError("Pi-hole response payload is not a JSON object")
        return decoded

    def _request_json_optional(self, path: str) -> dict[str, object]:
        """Best-effort request for optional enrichment endpoints."""
        try:
            return self._request_json(path)
        except RuntimeError as exc:
            _log.debug("Pi-hole optional endpoint unavailable: %s (%s)", path, exc)
            return {}

    def _active_session_sid(self) -> str:
        """Resolve the session sid to use for API requests."""
        if self._session_sid:
            return self._session_sid
        return self._password_session_sid

    def _can_refresh_password_session(self) -> bool:
        """Return whether password-based auth can refresh an expired sid."""
        return bool(self._password)

    def _ensure_password_session_sid(self) -> str:
        """Create and cache a password-derived session sid when needed."""
        if not self._password:
            return ""
        if self._password_session_sid:
            return self._password_session_sid

        url = f"{self._base_url}/api/auth"
        body = json.dumps({"password": self._password}).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        req = Request(  # noqa: S310 -- user-provided API endpoint
            url,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(  # noqa: S310 -- user-provided API endpoint
                req,
                timeout=self._timeout,
                context=self._ssl_context,
            ) as resp:
                raw = resp.read().decode("utf-8")
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise RuntimeError("Pi-hole password auth failed") from exc
            raise RuntimeError(f"Pi-hole auth request failed with HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"Pi-hole auth transport error: {exc}") from exc

        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Pi-hole auth JSON parse error: {exc}") from exc

        if not isinstance(decoded, dict):
            raise RuntimeError("Pi-hole auth payload is not a JSON object")

        sid = _first_text(decoded, [("session", "sid"), ("sid",)])
        if not sid:
            raise RuntimeError("Pi-hole auth response did not include a session sid")
        self._password_session_sid = sid
        return sid

    def _placeholder_sample(self) -> dict[str, StoreValue]:
        """Return a placeholder sample when API auth fails.

        This allows the template to display "-" for values instead of crashing,
        and gives the user time to fix credentials before data resumes.
        """
        return {
            "pihole.version": "—",
            "pihole.blocking.enabled": 0.0,
            "pihole.queries.total": 0.0,
            "pihole.queries.blocked": 0.0,
            "pihole.queries.blocked_percent": 0.0,
            "pihole.clients.active_count": 0.0,
            "pihole.domains.blocked_count": 0.0,
            "pihole.top_blocked.domain": "—",
            "pihole.top_blocked.hits": 0.0,
            "pihole.top_blocked.list": "—|—",
            "pihole.top_client.name": "—",
            "pihole.top_client.queries": 0.0,
            "pihole.top_client.list": "—|—",
        }


def _extract_blocking_enabled(payload: dict[str, object]) -> bool:
    """Extract blocking on/off state across multiple Pi-hole payload shapes."""
    enabled: bool | None = None

    direct = _nested(payload, ("blocking", "enabled"))
    if isinstance(direct, bool):
        enabled = direct
    elif isinstance(direct, int | float):
        enabled = direct != 0
    else:
        status_value = _nested(payload, ("status",))
        if isinstance(status_value, str):
            text = status_value.strip().lower()
            if text in {"enabled", "on", "true", "1"}:
                enabled = True
            elif text in {"disabled", "off", "false", "0"}:
                enabled = False

    if enabled is None:
        scalar = _nested(payload, ("blocking",))
        if isinstance(scalar, bool):
            enabled = scalar
        elif isinstance(scalar, int | float):
            enabled = scalar != 0

    if enabled is None:
        return True
    return enabled


def _extract_top_entry(
    payload: dict[str, object],
    *,
    keys: tuple[tuple[str, ...], ...],
    name_fields: tuple[str, ...],
    value_fields: tuple[str, ...],
) -> tuple[str, float]:
    """Extract top-row name/value from list- or dict-based API fragments."""
    node: object = None
    for path in keys:
        node = _nested(payload, path)
        if node is not None:
            break

    if isinstance(node, list) and node:
        first = node[0]
        if isinstance(first, dict):
            name = _dict_text(first, name_fields)
            value = _dict_number(first, value_fields)
            return name, value

    if isinstance(node, dict) and node:
        first_key = next(iter(node.keys()))
        first_val = node[first_key]
        name = first_key if isinstance(first_key, str) else ""
        return name, _to_float(first_val)

    return "", 0.0


def _extract_top_entries(
    payload: dict[str, object],
    *,
    keys: tuple[tuple[str, ...], ...],
    name_fields: tuple[str, ...],
    value_fields: tuple[str, ...],
    limit: int,
) -> list[tuple[str, float]]:
    """Extract ranked top rows from list- or dict-based API fragments."""
    node: object = None
    for path in keys:
        node = _nested(payload, path)
        if node is not None:
            break

    rows: list[tuple[str, float]] = []
    if isinstance(node, list):
        for item in node:
            if not isinstance(item, dict):
                continue
            name = _dict_text(item, name_fields)
            if not name:
                continue
            value = _dict_number(item, value_fields)
            rows.append((name, value))
            if len(rows) >= limit:
                break
        return rows

    if isinstance(node, dict):
        for key, value in node.items():
            if not isinstance(key, str):
                continue
            rows.append((key, _to_float(value)))
            if len(rows) >= limit:
                break

    return rows


def _format_ranked_list(rows: list[tuple[str, float]]) -> str:
    """Format top-row tuples into a multiline two-column table payload."""
    if not rows:
        return "—|—"

    lines: list[str] = []
    for name, count in rows:
        lines.append(f"{name}|{int(count)}")
    return "\n".join(lines)


def _first_number(payload: dict[str, object], keys: list[tuple[str, ...]]) -> float:
    """Return first numeric value found in candidate key paths."""
    for path in keys:
        value = _nested(payload, path)
        if value is None:
            continue
        return _to_float(value)
    return 0.0


def _first_text(payload: dict[str, object], keys: list[tuple[str, ...]]) -> str:
    """Return first non-empty text found in candidate key paths."""
    for path in keys:
        value = _nested(payload, path)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
    return ""


def _dict_text(raw: dict[str, object], keys: tuple[str, ...]) -> str:
    """Read first non-empty text from a dict by candidate keys."""
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
    return ""


def _dict_number(raw: dict[str, object], keys: tuple[str, ...]) -> float:
    """Read first numeric-like value from a dict by candidate keys."""
    for key in keys:
        value = raw.get(key)
        if value is not None:
            return _to_float(value)
    return 0.0


def _nested(payload: dict[str, object], path: tuple[str, ...]) -> object | None:
    """Read a nested dict path safely."""
    node: object = payload
    for part in path:
        if not isinstance(node, dict):
            return None
        node = node.get(part)
        if node is None:
            return None
    return node


def _to_float(value: object) -> float:
    """Convert Pi-hole scalar payload values to float with safe fallback."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace("%", "")
        if not text:
            return 0.0
        try:
            return float(text)
        except ValueError:
            return 0.0
    return 0.0
