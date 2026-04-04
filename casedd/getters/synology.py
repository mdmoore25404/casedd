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


def _status_level(status_text: str) -> str:
    """Normalize storage status text into a small severity set."""
    normalized = status_text.strip().lower()
    if normalized in {"critical", "error", "failed", "crashed", "down"}:
        return "critical"
    if normalized in {"warning", "degraded", "repairing", "scrubbing"}:
        return "warning"
    return "ok"


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
        include_dsm_updates: bool = True,
    ) -> None:
        """Initialize Synology getter settings."""
        super().__init__(store, interval)
        self._host = host.rstrip("/")
        self._username = username.strip() if isinstance(username, str) else ""
        self._password = password.strip() if isinstance(password, str) else ""
        self._sid = sid.strip() if isinstance(sid, str) else ""
        self._timeout = timeout
        self._include_surveillance = include_surveillance
        self._surveillance_max_cameras = max(0, surveillance_max_cameras)
        self._include_camera_snapshots = include_camera_snapshots
        self._camera_snapshot_width = max(0, camera_snapshot_width)
        self._camera_snapshot_height = max(0, camera_snapshot_height)
        self._include_dsm_updates = include_dsm_updates
        self._volume_filter = _compile_regex(volume_exclude_regex, "volume_exclude")
        self._user_filter = _compile_regex(user_exclude_regex, "user_exclude")
        self._ssl_context: ssl.SSLContext | None = None
        if self._host.startswith("https://") and not verify_tls:
            self._ssl_context = ssl._create_unverified_context()  # noqa: S323

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
            payload.update(self._sample_storage(sid))
            payload.update(self._sample_services(sid))
            payload.update(self._sample_users(sid))
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
            "synology.users.count": 0.0,
            "synology.users.rows": "",
            "synology.surveillance.available": 0.0,
            "synology.surveillance.camera_count": 0.0,
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
        data = self._call_first(
            (
                _ApiCall("SYNO.Core.System", "info", 1, {}),
                _ApiCall("SYNO.DSM.Info", "getinfo", 2, {}),
            ),
            sid,
        )

        hostname = _first_text(data, (("hostname",), ("host_name",), ("server_name",)))
        model = _first_text(data, (("model",), ("product_model",), ("platform",)))
        version = _first_text(
            data,
            (("version_string",), ("productversion",), ("version",), ("buildnumber",)),
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
                _ApiCall("SYNO.Core.Upgrade.Server", "check", 1, {}),
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
            (("storage_pools",), ("storagepool",), ("data", "storage_pools")),
        )
        disks = _first_list(data, (("disks",), ("disk",), ("data", "disks")))

        payload: dict[str, StoreValue] = {
            "synology.storage.warning_count": 0.0,
            "synology.storage.critical_count": 0.0,
        }

        warning_count = 0.0
        critical_count = 0.0
        for disk_obj in disks:
            disk = _as_dict(disk_obj)
            status = _first_text(disk, (("status",), ("health",), ("state",)))
            level = _status_level(status)
            if level == "critical":
                critical_count += 1.0
            elif level == "warning":
                warning_count += 1.0

        payload["synology.storage.warning_count"] = warning_count
        payload["synology.storage.critical_count"] = critical_count

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
                (("total_size",), ("size_total",), ("total",), ("size",)),
            )
            used_bytes = _first_float(
                volume,
                (("used_size",), ("size_used",), ("used",), ("used_space",)),
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
            status = _first_text(pool, (("status",), ("health",), ("state",)))
            key_prefix = f"synology.storagepool_{index}"
            payload[f"{key_prefix}.name"] = name
            payload[f"{key_prefix}.status"] = status

        return payload

    def _sample_services(self, sid: str) -> dict[str, StoreValue]:
        """Collect selected DSM service states."""
        data = self._call_first(
            (
                _ApiCall("SYNO.Core.Service", "list", 1, {"limit": 200, "offset": 0}),
            ),
            sid,
        )
        services = _first_list(data, (("services",), ("service",), ("data", "services")))
        defaults: dict[str, StoreValue] = {
            "synology.services.smb_state": "unknown",
            "synology.services.file_station_state": "unknown",
            "synology.services.synology_drive_state": "unknown",
            "synology.services.surveillance_station_state": "unknown",
        }
        if not services:
            return defaults

        targets = {
            "smb": "synology.services.smb_state",
            "file station": "synology.services.file_station_state",
            "synology drive": "synology.services.synology_drive_state",
            "surveillance station": "synology.services.surveillance_station_state",
        }

        for service_obj in services:
            service = _as_dict(service_obj)
            name = _first_text(service, (("display_name",), ("service",), ("name",))).lower()
            if not name:
                continue
            state_text = _first_text(
                service,
                (("status",), ("state",), ("running",), ("enable",)),
            ).lower()
            normalized_state = "unknown"
            if state_text in {"1", "true", "running", "started", "on"}:
                normalized_state = "running"
            elif state_text in {"0", "false", "stopped", "off", "disabled"}:
                normalized_state = "stopped"
            for token, key in targets.items():
                if token in name:
                    defaults[key] = normalized_state
                    break

        return defaults

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
            "synology.users.rows": "\n".join(f"{name}|active" for name in names[:10]),
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
                        "limit": self._surveillance_max_cameras,
                    },
                ),
                _ApiCall(
                    "SYNO.SurveillanceStation.Camera",
                    "list",
                    9,
                    {
                        "offset": 0,
                        "limit": self._surveillance_max_cameras,
                    },
                ),
            ),
            sid,
        )
        cameras = _first_list(camera_data, (("cameras",), ("camera",), ("data", "cameras")))

        payload: dict[str, StoreValue] = {
            "synology.surveillance.available": 1.0,
            "synology.surveillance.camera_count": float(len(cameras)),
            "synology.surveillance.recording_count": 0.0,
        }

        rows: list[str] = []
        recording_count = 0.0
        for index, camera_obj in enumerate(cameras[: self._surveillance_max_cameras], start=1):
            camera = _as_dict(camera_obj)
            name = _first_text(camera, (("name",), ("cameraName",), ("id",)))
            camera_id = _first_text(camera, (("id",), ("camera_id",), ("camId",)))
            status = _first_text(camera, (("status",), ("enabled",), ("recording",))).lower()
            normalized_status = "online"
            if status in {"0", "false", "disabled", "offline"}:
                normalized_status = "offline"
            elif status in {"recording", "true", "1"}:
                normalized_status = "recording"
            if normalized_status == "recording":
                recording_count += 1.0

            snapshot_url = ""
            if self._include_camera_snapshots and camera_id:
                snapshot_url = self._snapshot_url(camera_id, sid)

            key_prefix = f"synology.camera_{index}"
            payload[f"{key_prefix}.name"] = name
            payload[f"{key_prefix}.status"] = normalized_status
            payload[f"{key_prefix}.snapshot_url"] = snapshot_url
            rows.append(f"{name}|{normalized_status}")

        payload["synology.surveillance.recording_count"] = recording_count
        payload["synology.cameras.rows"] = "\n".join(rows)
        return payload

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
        return f"{self._host}/webapi/entry.cgi?{urlencode(query)}"
