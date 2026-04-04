"""Synology DSM and Surveillance Station getter.

Polls Synology DSM Web API endpoints and publishes flattened ``synology.*``
keys for dashboard widgets.

Store keys written include:
    - ``synology.auth.ok``
    - ``synology.system.reachable``
    - ``synology.system.hostname``
    - ``synology.system.model``
    - ``synology.system.version``
    - ``synology.dsm.update_available``
    - ``synology.dsm.latest_version``
    - ``synology.storage.warning_count``
    - ``synology.storage.critical_count``
    - ``synology.disks.rows``
    - ``synology.shares.rows``
    - ``synology.performance.cpu_percent``
    - ``synology.performance.ram_percent``
    - ``synology.performance.net_rx_kbps``
    - ``synology.performance.net_tx_kbps``
    - ``synology.performance.disk_read_mb_s``
    - ``synology.performance.disk_write_mb_s``
    - ``synology.volume_<n>.name``
    - ``synology.volume_<n>.used_percent``
    - ``synology.volume_<n>.free_tb``
    - ``synology.storagepool_<n>.status``
    - ``synology.users.count``
    - ``synology.user_<n>.name``
    - ``synology.services.smb_state``
    - ``synology.services.file_station_state``
    - ``synology.services.synology_drive_state``
    - ``synology.services.surveillance_station_state``
    - ``synology.services.rows``
    - ``synology.status.rows``
    - ``synology.surveillance.available``
    - ``synology.surveillance.camera_count``
    - ``synology.camera_<n>.name``
    - ``synology.camera_<n>.status``
    - ``synology.camera_<n>.snapshot_url``
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import re
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ApiCall:
    """One DSM API request descriptor."""

    api: str
    method: str
    version: int
    params: dict[str, object]


class _SynologyAuthError(RuntimeError):
    """Raised when Synology auth/session state is invalid."""


def _as_dict(value: object) -> dict[str, object]:
    """Return *value* as ``dict[str, object]`` when possible."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(key, str):
            out[key] = item
    return out


def _as_list(value: object) -> list[object]:
    """Return *value* as ``list[object]`` when possible."""
    if isinstance(value, list):
        return value
    return []


def _as_text(value: object) -> str:
    """Normalize a scalar object to text."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return ""
    if isinstance(value, int | float):
        return str(value)
    return ""


def _as_float(value: object) -> float | None:
    """Coerce scalar object to float."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def _nested_value(payload: dict[str, object], path: tuple[str, ...]) -> object | None:
    """Read a nested payload value by key path."""
    cursor: object = payload
    for key in path:
        if not isinstance(cursor, dict):
            return None
        mapped = _as_dict(cursor)
        if key not in mapped:
            return None
        cursor = mapped[key]
    return cursor


def _first_text(payload: dict[str, object], paths: tuple[tuple[str, ...], ...]) -> str:
    """Return first non-empty text found in *paths*."""
    for path in paths:
        text = _as_text(_nested_value(payload, path))
        if text:
            return text
    return ""


def _first_float(payload: dict[str, object], paths: tuple[tuple[str, ...], ...]) -> float | None:
    """Return first numeric value found in *paths*."""
    for path in paths:
        number = _as_float(_nested_value(payload, path))
        if number is not None:
            return number
    return None


def _first_list(payload: dict[str, object], paths: tuple[tuple[str, ...], ...]) -> list[object]:
    """Return first non-empty list found in *paths*."""
    for path in paths:
        values = _as_list(_nested_value(payload, path))
        if values:
            return values
    return []


def _bytes_to_gb(value: float | None) -> float:
    """Convert bytes to GiB, handling unknown values."""
    if value is None or value <= 0:
        return 0.0
    return value / (1024.0 ** 3)


def _compile_regex(pattern: str | None, label: str) -> re.Pattern[str] | None:
    """Compile regex filter pattern when configured."""
    if pattern is None:
        return None
    value = pattern.strip()
    if not value:
        return None
    try:
        return re.compile(value)
    except re.error as exc:
        _log.warning("Ignoring invalid Synology %s regex '%s': %s", label, value, exc)
        return None


def _parse_status_set(raw: str | None) -> set[str]:
    """Parse comma-delimited camera status tokens to a normalized set."""
    if raw is None:
        return set()
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _status_level(status_text: str) -> str:
    """Normalize storage status text into a small severity set."""
    normalized = status_text.strip().lower()
    if normalized in {"critical", "error", "failed", "crashed", "down"}:
        return "critical"
    if normalized in {"warning", "degraded", "repairing", "scrubbing"}:
        return "warning"
    return "ok"


def _icon_for_state(state_text: str) -> str:
    """Map normalized service-like state text to table icon key."""
    lowered = state_text.strip().lower()
    if lowered in {
        "running",
        "online",
        "recording",
        "latest",
        "ok",
        "normal",
        "enabled",
    }:
        return "started"
    if lowered in {
        "stopped",
        "offline",
        "error",
        "failed",
        "available",
        "critical",
        "disabled",
    }:
        return "exited"
    return "unknown"


def _normalize_state_text(state_text: str) -> str:
    """Normalize service/package status text to compact operational states."""
    lowered = state_text.strip().lower()
    if lowered in {"enabled", "running", "on", "static", "1", "true", "normal"}:
        return "running"
    if lowered in {"disabled", "off", "stopped", "0", "false", "error", "failed"}:
        return "stopped"
    return "unknown"


def _camera_state(status_text: str) -> str:
    """Normalize camera status values from numeric/string payload forms."""
    lowered = status_text.strip().lower()
    if lowered in {"1", "online", "enabled", "true"}:
        return "online"
    if lowered in {"2", "recording", "rec", "record", "true_recording"}:
        return "recording"
    if lowered in {"0", "7", "offline", "disabled", "false"}:
        return "offline"
    return "unknown"


def _compact_state(state_text: str) -> str:
    """Return a compact uppercase state token for dense table rows."""
    lowered = state_text.strip().lower()
    if lowered in {"normal", "ok", "latest", "running", "online"}:
        return "OK"
    if lowered in {"warning", "degraded"}:
        return "WARN"
    if lowered in {"critical", "failed", "error", "offline", "available"}:
        return "ALERT"
    if lowered == "not installed":
        return "N/A"
    if lowered:
        return lowered.upper()
    return "UNK"


def _snapshot_host(base_url: str, verify_tls: bool) -> str:
    """Return the host used for camera snapshot URLs.

    When TLS verification is disabled, prefer an HTTP snapshot URL to avoid
    certificate trust failures in the generic image widget fetch path.
    """
    _ = verify_tls
    return base_url.rstrip("/")


class SynologyGetter(BaseGetter):
    """Getter for Synology DSM and Surveillance Station telemetry.

    Args:
        store: Shared data store.
        host: Synology DSM base URL (for example ``http://nas1:5000``).
        username: DSM username for API login.
        password: DSM password for API login.
        sid: Optional pre-authenticated DSM session ID.
        interval: Poll interval in seconds.
        timeout: HTTP timeout in seconds.
        verify_tls: Verify TLS certificates for HTTPS endpoints.
        volume_exclude_regex: Optional regex to exclude volume rows by name.
        user_exclude_regex: Optional regex to exclude users by username.
        include_surveillance: Poll Surveillance Station data when true.
        surveillance_max_cameras: Max camera rows exposed to the store.
        include_camera_snapshots: Publish camera snapshot URLs when true.
        camera_snapshot_width: Optional snapshot width hint.
        camera_snapshot_height: Optional snapshot height hint.
        camera_include_regex: Optional regex to include camera names/ids.
        camera_exclude_regex: Optional regex to exclude camera names/ids.
        camera_exclude_statuses: Comma-delimited raw/normalized statuses to skip.
        include_dsm_updates: Poll DSM update status when true.
    """

    def __init__(  # noqa: PLR0913 -- explicit config mapping is preferable
        self,
        store: DataStore,
        host: str = "",
        username: str | None = None,
        password: str | None = None,
        sid: str | None = None,
        interval: float = 20.0,
        timeout: float = 5.0,
        verify_tls: bool = True,
        volume_exclude_regex: str | None = None,
        user_exclude_regex: str | None = None,
        include_surveillance: bool = True,
        surveillance_max_cameras: int = 4,
        include_camera_snapshots: bool = True,
        camera_snapshot_width: int = 640,
        camera_snapshot_height: int = 360,
        camera_include_regex: str | None = None,
        camera_exclude_regex: str | None = None,
        camera_exclude_statuses: str | None = "7",
        include_dsm_updates: bool = True,
    ) -> None:
        """Initialize Synology getter settings."""
        super().__init__(store, interval)
        self._host = host.rstrip("/")
        self._username = username.strip() if isinstance(username, str) else ""
        self._password = password.strip() if isinstance(password, str) else ""
        self._sid = sid.strip() if isinstance(sid, str) else ""
        self._timeout = timeout
        self._verify_tls = verify_tls
        self._include_surveillance = include_surveillance
        self._surveillance_max_cameras = max(0, surveillance_max_cameras)
        self._include_camera_snapshots = include_camera_snapshots
        self._camera_snapshot_width = max(0, camera_snapshot_width)
        self._camera_snapshot_height = max(0, camera_snapshot_height)
        self._include_dsm_updates = include_dsm_updates
        self._volume_filter = _compile_regex(volume_exclude_regex, "volume_exclude")
        self._user_filter = _compile_regex(user_exclude_regex, "user_exclude")
        self._camera_include_filter = _compile_regex(camera_include_regex, "camera_include")
        self._camera_exclude_filter = _compile_regex(camera_exclude_regex, "camera_exclude")
        self._camera_exclude_statuses = _parse_status_set(camera_exclude_statuses)
        self._ssl_context: ssl.SSLContext | None = None
        if self._host.startswith("https://") and not verify_tls:
            self._ssl_context = ssl._create_unverified_context()  # noqa: S323
        self._snapshot_host = _snapshot_host(self._host, verify_tls)

    async def fetch(self) -> dict[str, StoreValue]:
        """Collect one Synology sample and flatten it to ``synology.*`` keys."""
        return await asyncio.to_thread(self._sample)

    def _sample(self) -> dict[str, StoreValue]:
        """Blocking Synology poll implementation."""
        try:
            sid = self._ensure_sid()
            payload: dict[str, StoreValue] = {
                "synology.auth.ok": 1.0,
                "synology.system.reachable": 1.0,
            }
            payload.update(self._sample_system(sid))
            payload.update(self._sample_utilization(sid))
            payload.update(self._sample_storage(sid))
            payload.update(self._sample_services(sid))
            payload.update(self._sample_users(sid))
            payload.update(self._sample_shares(sid))
            if self._include_dsm_updates:
                payload.update(self._sample_dsm_updates(sid))
            else:
                payload["synology.dsm.update_available"] = -1.0
                payload["synology.dsm.latest_version"] = ""
            if self._include_surveillance:
                payload.update(self._sample_surveillance(sid))
            else:
                payload["synology.surveillance.available"] = 0.0
                payload["synology.surveillance.camera_count"] = 0.0
            payload["synology.status.rows"] = self._compose_status_rows(payload)
            payload["synology.surveillance.status.rows"] = self._compose_surveillance_status_rows(
                payload
            )
            return payload
        except _SynologyAuthError:
            return self._auth_placeholder()

    def _auth_placeholder(self) -> dict[str, StoreValue]:
        """Return stable placeholder keys for auth failures."""
        return {
            "synology.auth.ok": 0.0,
            "synology.system.reachable": 0.0,
            "synology.system.hostname": "",
            "synology.system.model": "",
            "synology.system.version": "",
            "synology.dsm.update_available": -1.0,
            "synology.dsm.latest_version": "",
            "synology.storage.warning_count": 0.0,
            "synology.storage.critical_count": 0.0,
            "synology.disks.rows": "",
            "synology.shares.rows": "",
            "synology.performance.disk_read_mb_s": 0.0,
            "synology.performance.disk_write_mb_s": 0.0,
            "synology.users.count": 0.0,
            "synology.users.rows": "",
            "synology.surveillance.available": 0.0,
            "synology.surveillance.camera_count": 0.0,
            "synology.services.rows": "",
            "synology.status.rows": "",
            "synology.surveillance.status.rows": "",
            "synology.services.hyper_backup_state": "unknown",
            "synology.services.active_backup_state": "unknown",
        }

    def _sample_utilization(self, sid: str) -> dict[str, StoreValue]:
        """Collect CPU/RAM/network utilization metrics from DSM."""
        data = self._call_first((
            _ApiCall("SYNO.Core.System.Utilization", "get", 1, {}),
        ), sid)
        if not data:
            return {
                "synology.performance.cpu_percent": 0.0,
                "synology.performance.ram_percent": 0.0,
                "synology.performance.net_rx_kbps": 0.0,
                "synology.performance.net_tx_kbps": 0.0,
                "synology.performance.disk_read_mb_s": 0.0,
                "synology.performance.disk_write_mb_s": 0.0,
            }

        cpu_data = _as_dict(data.get("cpu"))
        memory_data = _as_dict(data.get("memory"))
        network_rows = _as_list(data.get("network"))
        disk_block = _as_dict(data.get("disk"))
        disk_rows = _as_list(disk_block.get("disk"))

        cpu_percent = _first_float(cpu_data, (("user_load",), ("system_load",), ("1min_load",)))
        if cpu_percent is None:
            cpu_percent = 0.0
        else:
            other_load = _as_float(cpu_data.get("other_load"))
            if other_load is not None:
                cpu_percent += other_load

        ram_percent = _first_float(memory_data, (("real_usage",), ("usage",)))
        if ram_percent is None:
            ram_percent = 0.0

        rx_bytes = 0.0
        tx_bytes = 0.0
        for row_obj in network_rows:
            row = _as_dict(row_obj)
            device_name = _first_text(row, (("device",),)).lower()
            if device_name and device_name != "total":
                continue
            rx_value = _first_float(row, (("rx",), ("rx_byte",), ("rx_bytes",)))
            tx_value = _first_float(row, (("tx",), ("tx_byte",), ("tx_bytes",)))
            if rx_value is not None:
                rx_bytes = rx_value
            if tx_value is not None:
                tx_bytes = tx_value
            break

        disk_read_bytes = 0.0
        disk_write_bytes = 0.0
        for disk_obj in disk_rows:
            disk_row = _as_dict(disk_obj)
            read_value = _first_float(disk_row, (("read_byte",), ("read_bytes",), ("read",)))
            write_value = _first_float(
                disk_row,
                (("write_byte",), ("write_bytes",), ("write",)),
            )
            if read_value is not None:
                disk_read_bytes += read_value
            if write_value is not None:
                disk_write_bytes += write_value

        return {
            "synology.performance.cpu_percent": round(max(0.0, cpu_percent), 2),
            "synology.performance.ram_percent": round(max(0.0, ram_percent), 2),
            "synology.performance.net_rx_kbps": round(max(0.0, rx_bytes) / 1024.0, 2),
            "synology.performance.net_tx_kbps": round(max(0.0, tx_bytes) / 1024.0, 2),
            "synology.performance.disk_read_mb_s": round(max(0.0, disk_read_bytes) / 1048576.0, 3),
            "synology.performance.disk_write_mb_s": round(
                max(0.0, disk_write_bytes) / 1048576.0,
                3,
            ),
        }

    def _ensure_sid(self) -> str:
        """Return active DSM sid, logging in when needed."""
        if not self._host:
            raise _SynologyAuthError("Synology host is not configured")
        if self._sid:
            return self._sid
        if not self._username or not self._password:
            raise _SynologyAuthError("Synology credentials are not configured")

        payload = self._request_json(
            "/webapi/auth.cgi",
            {
                "api": "SYNO.API.Auth",
                "version": "6",
                "method": "login",
                "account": self._username,
                "passwd": self._password,
                "session": "CASEDD",
                "format": "sid",
            },
        )
        success = payload.get("success") is True
        if not success:
            raise _SynologyAuthError("Synology authentication failed")

        data = _as_dict(payload.get("data"))
        sid = _as_text(data.get("sid"))
        if not sid:
            raise _SynologyAuthError("Synology auth returned no sid")

        self._sid = sid
        return sid

    def _request_json(self, path: str, query: dict[str, object]) -> dict[str, object]:
        """Execute one Synology API request and parse JSON response."""
        encoded = urlencode({key: str(value) for key, value in query.items()})
        url = f"{self._host}{path}?{encoded}"
        request = Request(url, method="GET")  # noqa: S310 -- user-configured Synology endpoint
        try:
            with urlopen(  # noqa: S310 -- user-configured Synology endpoint
                request,
                timeout=self._timeout,
                context=self._ssl_context,
            ) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            if exc.code in {401, 403}:
                self._sid = ""
                raise _SynologyAuthError("Synology auth failed") from exc
            raise RuntimeError(f"Synology HTTP error {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"Synology transport error: {exc}") from exc

        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Synology JSON parse error: {exc}") from exc

        if not isinstance(decoded, dict):
            raise RuntimeError("Synology response was not a JSON object")
        return _as_dict(decoded)

    def _call_entry(
        self,
        api: str,
        method: str,
        version: int,
        params: dict[str, object],
        sid: str,
    ) -> dict[str, object]:
        """Call one API via ``/webapi/entry.cgi`` with sid authentication."""
        query: dict[str, object] = {
            "api": api,
            "method": method,
            "version": version,
            "_sid": sid,
        }
        query.update(params)
        payload = self._request_json("/webapi/entry.cgi", query)
        if payload.get("success") is True:
            return _as_dict(payload.get("data"))

        error = _as_dict(payload.get("error"))
        code = _as_float(error.get("code"))
        if code in {105.0, 106.0, 107.0, 119.0}:
            self._sid = ""
            raise _SynologyAuthError("Synology session expired")

        message = f"Synology API call failed: {api}.{method}"
        raise RuntimeError(message)

    def _call_first(self, calls: tuple[_ApiCall, ...], sid: str) -> dict[str, object]:
        """Try API calls in order and return first successful payload."""
        for call in calls:
            try:
                payload = self._call_entry(
                    call.api,
                    call.method,
                    call.version,
                    call.params,
                    sid,
                )
            except RuntimeError:
                continue
            if payload:
                return payload
        return {}

    def _sample_system(self, sid: str) -> dict[str, StoreValue]:
        """Collect system identity details."""
        system_data = self._call_first((
            _ApiCall("SYNO.Core.System", "info", 1, {}),
        ), sid)
        dsm_data = self._call_first((
            _ApiCall("SYNO.DSM.Info", "getinfo", 2, {}),
        ), sid)
        network_data = self._call_first(
            (
                _ApiCall("SYNO.Core.Network", "get", 2, {}),
                _ApiCall("SYNO.Core.Network", "get", 1, {}),
            ),
            sid,
        )

        hostname = _first_text(
            {"network": network_data, "system": system_data, "dsm": dsm_data},
            (
                ("network", "server_name"),
                ("network", "hostname"),
                ("system", "hostname"),
                ("dsm", "server_name"),
            ),
        )
        model = _first_text(
            {"dsm": dsm_data, "system": system_data},
            (
                ("dsm", "model"),
                ("system", "model"),
                ("system", "product_model"),
                ("system", "platform"),
            ),
        )
        version = _first_text(
            {"dsm": dsm_data, "system": system_data},
            (
                ("dsm", "version_string"),
                ("dsm", "version"),
                ("system", "version_string"),
                ("system", "firmware_ver"),
                ("system", "buildnumber"),
            ),
        )

        return {
            "synology.system.hostname": hostname,
            "synology.system.model": model,
            "synology.system.version": version,
        }

    def _sample_dsm_updates(self, sid: str) -> dict[str, StoreValue]:
        """Collect DSM update availability state."""
        data = self._call_first(
            (
                _ApiCall("SYNO.Core.Upgrade.Server", "check", 4, {}),
                _ApiCall("SYNO.Core.Upgrade.Server", "check", 1, {}),
                _ApiCall("SYNO.Core.Upgrade", "check", 2, {}),
                _ApiCall("SYNO.Core.Upgrade", "check", 1, {}),
            ),
            sid,
        )
        if not data:
            return {
                "synology.dsm.update_available": -1.0,
                "synology.dsm.latest_version": "",
            }

        update_obj = _as_dict(data.get("update"))
        available_number = _first_float(
            {"root": update_obj, "self": data},
            (
                ("root", "available"),
                ("root", "upgrade_available"),
                ("self", "upgrade_available"),
                ("self", "available"),
            ),
        )
        if available_number is None:
            available_text = _first_text(
                {"root": update_obj, "self": data},
                (
                    ("root", "status"),
                    ("self", "status"),
                ),
            ).lower()
            available = 1.0 if available_text in {"available", "upgrade"} else 0.0
        else:
            available = 1.0 if available_number > 0 else 0.0

        latest_version = _first_text(
            {"root": update_obj, "self": data},
            (
                ("root", "version"),
                ("root", "latest_version"),
                ("self", "latest_version"),
            ),
        )

        return {
            "synology.dsm.update_available": available,
            "synology.dsm.latest_version": latest_version,
        }

    def _sample_storage(self, sid: str) -> dict[str, StoreValue]:
        """Collect volume, pool, and disk-health storage metrics."""
        data = self._call_first(
            (
                _ApiCall("SYNO.Storage.CGI.Storage", "load_info", 1, {}),
                _ApiCall("SYNO.Core.Storage.Volume", "list", 1, {}),
            ),
            sid,
        )

        volumes = _first_list(data, (("volumes",), ("volume",), ("data", "volumes")))
        pools = _first_list(
            data,
            (
                ("storagePools",),
                ("storage_pools",),
                ("storagepool",),
                ("data", "storage_pools"),
            ),
        )
        disks = _first_list(
            data,
            (("disks",), ("disk",), ("disk_info",), ("data", "disks")),
        )

        payload: dict[str, StoreValue] = {
            "synology.storage.warning_count": 0.0,
            "synology.storage.critical_count": 0.0,
            "synology.disks.rows": "",
        }

        warning_count, critical_count, disk_rows = self._summarize_disks(disks)

        payload["synology.storage.warning_count"] = warning_count
        payload["synology.storage.critical_count"] = critical_count
        payload["synology.disks.rows"] = "\n".join(disk_rows[:10])

        filtered_volumes: list[dict[str, object]] = []
        for volume_obj in volumes:
            volume = _as_dict(volume_obj)
            name = _first_text(volume, (("name",), ("display_name",), ("id",), ("path",)))
            if self._volume_filter is not None and self._volume_filter.search(name):
                continue
            filtered_volumes.append(volume)

        payload["synology.volume.count"] = float(len(filtered_volumes))
        for index, volume in enumerate(filtered_volumes, start=1):
            name = _first_text(volume, (("name",), ("display_name",), ("id",), ("path",)))
            status = _first_text(volume, (("status",), ("health",), ("state",)))

            total_bytes = _first_float(
                volume,
                (
                    ("total_size",),
                    ("size_total",),
                    ("total",),
                    ("size", "total"),
                    ("size",),
                ),
            )
            used_bytes = _first_float(
                volume,
                (
                    ("used_size",),
                    ("size_used",),
                    ("used",),
                    ("size", "used"),
                    ("used_space",),
                ),
            )
            used_percent = _first_float(
                volume,
                (("used_percent",), ("usage",), ("percent",), ("usage_percent",)),
            )
            if used_percent is None:
                if total_bytes is not None and used_bytes is not None and total_bytes > 0:
                    used_percent = (used_bytes / total_bytes) * 100.0
                else:
                    used_percent = 0.0

            total_gb = _bytes_to_gb(total_bytes)
            used_gb = _bytes_to_gb(used_bytes)
            free_gb = max(0.0, total_gb - used_gb)

            key_prefix = f"synology.volume_{index}"
            payload[f"{key_prefix}.name"] = name
            payload[f"{key_prefix}.status"] = status
            payload[f"{key_prefix}.used_percent"] = round(max(0.0, used_percent), 2)
            payload[f"{key_prefix}.total_tb"] = round(total_gb / 1024.0, 2)
            payload[f"{key_prefix}.free_tb"] = round(free_gb / 1024.0, 2)

        payload["synology.storagepool.count"] = float(len(pools))
        for index, pool_obj in enumerate(pools, start=1):
            pool = _as_dict(pool_obj)
            name = _first_text(pool, (("name",), ("id",), ("desc",)))
            status = _first_text(
                pool,
                (("status",), ("summary_status",), ("health",), ("state",)),
            )
            key_prefix = f"synology.storagepool_{index}"
            payload[f"{key_prefix}.name"] = name
            payload[f"{key_prefix}.status"] = status

        return payload

    def _summarize_disks(self, disks: list[object]) -> tuple[float, float, list[str]]:
        """Summarize disk health severity counts and compact row text."""
        warning_count = 0.0
        critical_count = 0.0
        disk_rows: list[str] = []

        for disk_obj in disks:
            disk = _as_dict(disk_obj)
            name = _first_text(disk, (("display_name",), ("name",), ("id",), ("device",)))
            status = _first_text(
                disk,
                (("status",), ("adv_status",), ("smart_status",), ("health",), ("state",)),
            )
            level = _status_level(status)
            if level == "critical":
                critical_count += 1.0
            elif level == "warning":
                warning_count += 1.0

            smart_state = _first_text(
                disk,
                (("smart_status",), ("smart", "status"), ("health",), ("adv_status",)),
            )
            smart_text = smart_state if smart_state else "unknown"
            error_count = self._disk_error_count(disk)
            temp_text = self._disk_temp_text(disk)
            disk_name = name if name else "Disk"
            status_compact = _compact_state(status)
            smart_compact = _compact_state(smart_text)
            row = f"{disk_name}|{status_compact}|S:{smart_compact} E:{error_count}{temp_text}"
            disk_rows.append(row)

        return warning_count, critical_count, disk_rows

    def _disk_error_count(self, disk: dict[str, object]) -> int:
        """Return combined bad-sector and UNC error counts for one disk row."""
        bad_sector = _first_float(disk, (("bad_sector",),))
        unc_error = _first_float(disk, (("unc",), ("errors",), ("error_count",)))
        bad_sector_count = int(bad_sector) if bad_sector is not None else 0
        unc_error_count = int(unc_error) if unc_error is not None else 0
        return bad_sector_count + unc_error_count

    def _disk_temp_text(self, disk: dict[str, object]) -> str:
        """Return disk temperature suffix text for table rows."""
        temp_value = _first_float(disk, (("temp",), ("temperature",)))
        if temp_value is None:
            return ""
        return f" {int(temp_value)}C"

    def _sample_services(self, sid: str) -> dict[str, StoreValue]:
        """Collect selected DSM service states."""
        service_data = self._call_first(
            (
                _ApiCall("SYNO.Core.Service", "get", 3, {}),
                _ApiCall("SYNO.Core.Service", "get", 2, {}),
                _ApiCall("SYNO.Core.Service", "list", 1, {"limit": 200, "offset": 0}),
            ),
            sid,
        )
        package_data = self._call_first((
            _ApiCall("SYNO.Core.Package", "list", 2, {}),
            _ApiCall("SYNO.Core.Package", "list", 1, {}),
        ), sid)
        services = _first_list(
            service_data,
            (("services",), ("service",), ("data", "services")),
        )
        packages = _first_list(
            package_data,
            (("packages",), ("package",), ("data", "packages")),
        )
        defaults: dict[str, StoreValue] = {
            "synology.services.smb_state": "unknown",
            "synology.services.file_station_state": "unknown",
            "synology.services.synology_drive_state": "unknown",
            "synology.services.surveillance_station_state": "unknown",
            "synology.services.hyper_backup_state": "unknown",
            "synology.services.active_backup_state": "unknown",
            "synology.services.rows": "",
        }
        service_ids = {
            _first_text(
                service,
                (("service_id",), ("display_name",), ("name",), ("service",)),
            ).lower(): _first_text(
                service,
                (("enable_status",), ("status",), ("state",), ("running",)),
            ).lower()
            for service in (_as_dict(item) for item in services)
            if _first_text(
                service,
                (("service_id",), ("display_name",), ("name",), ("service",)),
            )
        }

        smb_state = "unknown"
        file_station_state = "unknown"
        drive_state = "unknown"
        surveillance_state = "unknown"
        for service_id, state_text in service_ids.items():
            normalized_state = _normalize_state_text(state_text)

            if "smb" in service_id and smb_state == "unknown":
                smb_state = normalized_state
            if ("file station" in service_id or "filestation" in service_id) and (
                file_station_state == "unknown"
            ):
                file_station_state = normalized_state
            if ("synology drive" in service_id or "synologydrive" in service_id) and (
                drive_state == "unknown"
            ):
                drive_state = normalized_state
            if ("surveillance station" in service_id or "surveillancestation" in service_id) and (
                surveillance_state == "unknown"
            ):
                surveillance_state = normalized_state

        defaults["synology.services.smb_state"] = smb_state
        defaults["synology.services.file_station_state"] = file_station_state
        defaults["synology.services.synology_drive_state"] = drive_state
        defaults["synology.services.surveillance_station_state"] = surveillance_state

        package_rows = [_as_dict(item) for item in packages]
        package_ids = {
            _first_text(pkg, (("id",), ("package",), ("name",))).lower()
            for pkg in package_rows
        }
        self._apply_package_service_defaults(defaults, package_ids)
        self._apply_backup_package_states(defaults, package_rows, package_ids)

        service_rows = [
            "SMB|"
            f"{_icon_for_state(str(defaults['synology.services.smb_state']))}|"
            f"{defaults['synology.services.smb_state']}",
            "File Station|"
            f"{_icon_for_state(str(defaults['synology.services.file_station_state']))}|"
            f"{defaults['synology.services.file_station_state']}",
            "Drive|"
            f"{_icon_for_state(str(defaults['synology.services.synology_drive_state']))}|"
            f"{defaults['synology.services.synology_drive_state']}",
            "Surveillance|"
            f"{_icon_for_state(str(defaults['synology.services.surveillance_station_state']))}|"
            f"{defaults['synology.services.surveillance_station_state']}",
        ]
        defaults["synology.services.rows"] = "\n".join(service_rows)
        return defaults

    def _apply_package_service_defaults(
        self,
        defaults: dict[str, StoreValue],
        package_ids: set[str],
    ) -> None:
        """Apply package-presence fallbacks for core service state keys."""
        if (
            "filestation" in package_ids
            and defaults["synology.services.file_station_state"] == "unknown"
        ):
            defaults["synology.services.file_station_state"] = "running"

        has_drive_package = "synologydrive" in package_ids or "cloudstation" in package_ids
        if (
            has_drive_package
            and defaults["synology.services.synology_drive_state"] == "unknown"
        ):
            defaults["synology.services.synology_drive_state"] = "running"
        if defaults["synology.services.synology_drive_state"] == "unknown":
            defaults["synology.services.synology_drive_state"] = "not installed"

        if (
            "surveillancestation" in package_ids
            and defaults["synology.services.surveillance_station_state"] == "unknown"
        ):
            defaults["synology.services.surveillance_station_state"] = "running"

        if "smbservice" in package_ids and defaults["synology.services.smb_state"] == "unknown":
            defaults["synology.services.smb_state"] = "running"

    def _apply_backup_package_states(
        self,
        defaults: dict[str, StoreValue],
        package_rows: list[dict[str, object]],
        package_ids: set[str],
    ) -> None:
        """Apply backup package operational states from package inventory."""
        for package in package_rows:
            package_id = _first_text(package, (("id",), ("package",), ("name",))).lower()
            package_state_raw = _first_text(
                package,
                (("status",), ("state",), ("running",), ("status_display",)),
            )
            package_state = _normalize_state_text(package_state_raw)

            if "hyperbackup" in package_id:
                defaults["synology.services.hyper_backup_state"] = package_state
            if "activebackup" in package_id:
                defaults["synology.services.active_backup_state"] = package_state

        if (
            "hyperbackup" in package_ids
            and defaults["synology.services.hyper_backup_state"] == "unknown"
        ):
            defaults["synology.services.hyper_backup_state"] = "installed"

        has_active_backup = any("activebackup" in package_id for package_id in package_ids)
        if (
            has_active_backup
            and defaults["synology.services.active_backup_state"] == "unknown"
        ):
            defaults["synology.services.active_backup_state"] = "installed"

    def _sample_users(self, sid: str) -> dict[str, StoreValue]:
        """Collect DSM user list with optional regex exclusions."""
        data = self._call_first(
            (
                _ApiCall("SYNO.Core.User", "list", 1, {"limit": 200, "offset": 0}),
            ),
            sid,
        )
        users = _first_list(data, (("users",), ("user",), ("data", "users")))
        names: list[str] = []
        for user_obj in users:
            user = _as_dict(user_obj)
            username = _first_text(user, (("name",), ("username",), ("id",)))
            if not username:
                continue
            if self._user_filter is not None and self._user_filter.search(username):
                continue
            names.append(username)

        payload: dict[str, StoreValue] = {
            "synology.users.count": float(len(names)),
            "synology.users.rows": "\n".join(names[:10]),
        }
        for index, username in enumerate(names[:8], start=1):
            payload[f"synology.user_{index}.name"] = username
        return payload

    def _sample_surveillance(self, sid: str) -> dict[str, StoreValue]:
        """Collect Surveillance Station status and camera feed metadata."""
        info = self._call_first(
            (
                _ApiCall("SYNO.SurveillanceStation.Info", "getInfo", 1, {}),
                _ApiCall("SYNO.SurveillanceStation.Info", "GetInfo", 1, {}),
            ),
            sid,
        )
        if not info:
            return {
                "synology.surveillance.available": 0.0,
                "synology.surveillance.camera_count": 0.0,
                "synology.surveillance.recording_count": 0.0,
            }

        camera_data = self._call_first(
            (
                _ApiCall(
                    "SYNO.SurveillanceStation.Camera",
                    "List",
                    9,
                    {
                        "offset": 0,
                        "limit": max(self._surveillance_max_cameras * 4, 20),
                    },
                ),
                _ApiCall(
                    "SYNO.SurveillanceStation.Camera",
                    "list",
                    9,
                    {
                        "offset": 0,
                        "limit": max(self._surveillance_max_cameras * 4, 20),
                    },
                ),
            ),
            sid,
        )
        cameras = _first_list(camera_data, (("cameras",), ("camera",), ("data", "cameras")))

        payload: dict[str, StoreValue] = {
            "synology.surveillance.available": 1.0,
            "synology.surveillance.camera_count": 0.0,
            "synology.surveillance.recording_count": 0.0,
            "synology.status.rows": "",
        }

        rows: list[str] = []
        status_rows: list[str] = []
        recording_count = 0.0
        selected_cameras = self._select_surveillance_cameras(cameras)

        selected_slice = selected_cameras[: self._surveillance_max_cameras]
        payload["synology.surveillance.camera_count"] = float(len(selected_slice))
        for index, camera in enumerate(selected_slice, start=1):
            row_payload, table_row, status_row, is_recording = self._camera_row_payload(
                camera,
                index,
                sid,
            )
            payload.update(row_payload)
            rows.append(table_row)
            status_rows.append(status_row)
            if is_recording:
                recording_count += 1.0

        for index in range(len(selected_slice) + 1, self._surveillance_max_cameras + 1):
            payload[f"synology.camera_{index}.name"] = "no camera"
            payload[f"synology.camera_{index}.status"] = "absent"
            payload[f"synology.camera_{index}.snapshot_url"] = ""
            payload[f"synology.camera_{index}.captured_at"] = ""

        payload["synology.surveillance.recording_count"] = recording_count
        payload["synology.cameras.rows"] = "\n".join(rows)
        payload["synology.status.rows"] = "\n".join(status_rows)
        return payload

    def _select_surveillance_cameras(self, cameras: list[object]) -> list[dict[str, object]]:
        """Filter and prioritize surveillance cameras for rendering."""
        selected_cameras: list[dict[str, object]] = []
        eligible_cameras: list[dict[str, object]] = []
        for camera_obj in cameras:
            camera = _as_dict(camera_obj)
            camera_name = _first_text(
                camera,
                (("newName",), ("name",), ("cameraName",), ("id",)),
            )
            raw_state = _first_text(
                camera,
                (("status",), ("enabled",), ("recording",), ("recordingStatus",), ("camStatus",)),
            )
            state = _camera_state(raw_state)
            if self._camera_exclude_statuses and (
                raw_state.strip().lower() in self._camera_exclude_statuses
                or state in self._camera_exclude_statuses
            ):
                continue
            if self._camera_include_filter is not None and not self._camera_include_filter.search(
                camera_name
            ):
                continue
            if self._camera_exclude_filter is not None and self._camera_exclude_filter.search(
                camera_name
            ):
                continue
            eligible_cameras.append(camera)
            if state in {"online", "recording"}:
                selected_cameras.append(camera)
        if selected_cameras:
            return selected_cameras
        return eligible_cameras

    def _camera_row_payload(
        self,
        camera: dict[str, object],
        index: int,
        sid: str,
    ) -> tuple[dict[str, StoreValue], str, str, bool]:
        """Build per-camera payload row values and table status row."""
        name = _first_text(camera, (("newName",), ("name",), ("cameraName",), ("id",)))
        camera_id = _first_text(camera, (("id",), ("camera_id",), ("camId",)))
        status_raw = _first_text(
            camera,
            (("status",), ("enabled",), ("recording",), ("recordingStatus",), ("camStatus",)),
        )
        normalized_status = _camera_state(status_raw)
        snapshot_url = ""
        captured_at = ""
        if self._include_camera_snapshots and camera_id:
            snapshot_url = self._snapshot_url(camera_id, sid)
            captured_at = time.strftime("%H:%M:%S", time.localtime())

        key_prefix = f"synology.camera_{index}"
        row_payload: dict[str, StoreValue] = {
            f"{key_prefix}.name": name,
            f"{key_prefix}.status": normalized_status,
            f"{key_prefix}.snapshot_url": snapshot_url,
            f"{key_prefix}.captured_at": captured_at,
        }
        table_row = f"{name}|{normalized_status}"
        label_name = name if name else f"Camera {camera_id or index}"
        status_row = f"{label_name}|{_icon_for_state(normalized_status)}|{normalized_status}"
        return row_payload, table_row, status_row, normalized_status == "recording"

    def _sample_shares(self, sid: str) -> dict[str, StoreValue]:
        """Collect list of shares and mount volume context."""
        data = self._call_first(
            (
                _ApiCall("SYNO.Core.Share", "list", 1, {"offset": 0, "limit": 200}),
            ),
            sid,
        )
        shares = _first_list(data, (("shares",), ("data", "shares")))

        rows: list[str] = []
        for share_obj in shares:
            share = _as_dict(share_obj)
            name = _first_text(share, (("name",), ("id",)))
            volume_path = _first_text(share, (("vol_path",), ("path",), ("location",)))
            if not name:
                continue
            volume_label = volume_path.rsplit("/", maxsplit=1)[-1] if volume_path else "-"
            flags: list[str] = []
            readonly = _first_float(
                share,
                (("is_readonly",), ("read_only",), ("readonly",)),
            )
            encrypted = _first_float(
                share,
                (("is_encrypted",), ("encrypted",), ("encrypt",)),
            )
            recycle_bin = _first_float(
                share,
                (("enable_recycle_bin",), ("recycle_bin",), ("is_recycle_bin",)),
            )
            quota_bytes = _first_float(share, (("quota",), ("quota_bytes",)))

            flags.append("ro" if (readonly is not None and readonly > 0) else "rw")
            if encrypted is not None and encrypted > 0:
                flags.append("enc")
            if recycle_bin is not None and recycle_bin > 0:
                flags.append("rb")
            if quota_bytes is not None and quota_bytes > 0:
                quota_tb = _bytes_to_gb(quota_bytes) / 1024.0
                flags.append(f"q:{quota_tb:.2f}TB")

            rows.append(f"{name}|{volume_label}|{' '.join(flags)}")

        return {
            "synology.shares.count": float(len(rows)),
            "synology.shares.rows": "\n".join(rows[:20]),
        }

    def _compose_status_rows(self, payload: dict[str, StoreValue]) -> str:
        """Build dashboard status rows (DSM update + core services)."""
        rows: list[str] = []

        update_available_raw = payload.get("synology.dsm.update_available")
        update_available = _as_float(update_available_raw)
        update_state = "unknown"
        if update_available is not None:
            update_state = "available" if update_available > 0 else "latest"
        rows.append(f"DSM Update|{_icon_for_state(update_state)}|{update_state.upper()}")

        for service_key, label in (
            ("synology.services.smb_state", "SMB"),
            ("synology.services.file_station_state", "File Station"),
            ("synology.services.synology_drive_state", "Drive"),
            ("synology.services.surveillance_station_state", "Surveillance"),
            ("synology.services.hyper_backup_state", "Hyper Backup"),
            ("synology.services.active_backup_state", "Active Backup"),
        ):
            state_text = _as_text(payload.get(service_key)) or "unknown"
            rows.append(f"{label}|{_icon_for_state(state_text)}|{state_text}")

        return "\n".join(rows)

    def _compose_surveillance_status_rows(self, payload: dict[str, StoreValue]) -> str:
        """Build surveillance status rows including camera states."""
        rows: list[str] = []
        update_available_raw = payload.get("synology.dsm.update_available")
        update_available = _as_float(update_available_raw)
        update_state = "unknown"
        if update_available is not None:
            update_state = "available" if update_available > 0 else "latest"
        rows.append(f"DSM Update|{_icon_for_state(update_state)}|{update_state.upper()}")

        surveillance_state = _as_text(payload.get("synology.services.surveillance_station_state"))
        rows.append(
            "Surveillance|"
            f"{_icon_for_state(surveillance_state or 'unknown')}|"
            f"{surveillance_state or 'unknown'}"
        )

        camera_count = int(_as_float(payload.get("synology.surveillance.camera_count")) or 0)
        for index in range(1, camera_count + 1):
            name = _as_text(payload.get(f"synology.camera_{index}.name")) or f"Camera {index}"
            state_text = _as_text(payload.get(f"synology.camera_{index}.status")) or "unknown"
            rows.append(f"{name}|{_icon_for_state(state_text)}|{state_text}")

        return "\n".join(rows)

    def _snapshot_url(self, camera_id: str, sid: str) -> str:
        """Construct a one-shot camera snapshot URL for the image widget."""
        query: dict[str, object] = {
            "api": "SYNO.SurveillanceStation.Camera",
            "method": "GetSnapshot",
            "version": 9,
            "id": camera_id,
            "_sid": sid,
            # Change URL each poll so the image widget does not stay pinned to stale cache.
            "_ts": int(time.time()),
        }
        if self._camera_snapshot_width > 0:
            query["width"] = self._camera_snapshot_width
        if self._camera_snapshot_height > 0:
            query["height"] = self._camera_snapshot_height
        return f"{self._snapshot_host}/webapi/entry.cgi?{urlencode(query)}"
