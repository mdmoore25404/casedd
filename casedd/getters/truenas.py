"""TrueNAS storage system getter.

Polls TrueNAS REST API endpoints and publishes flattened ``truenas.*``
keys for dashboard widgets.

Store keys written include:
    - ``truenas.auth.ok``
    - ``truenas.system.reachable``
    - ``truenas.system.hostname``
    - ``truenas.system.model``
    - ``truenas.system.version``
    - ``truenas.system.uptime``
    - ``truenas.system.update_available``
    - ``truenas.system.update_status``
    - ``truenas.performance.cpu_temp_c``
    - ``truenas.pool_<n>.name``
    - ``truenas.pool_<n>.status``
    - ``truenas.pool_<n>.used_percent``
    - ``truenas.pool_<n>.free_tb``
    - ``truenas.pool_<n>.total_tb``
    - ``truenas.disk_<n>.name``
    - ``truenas.disk_<n>.status``
    - ``truenas.disk_<n>.size_tb``
    - ``truenas.disk_<n>.temp_c``
    - ``truenas.users.count``
    - ``truenas.disks.rows``
    - ``truenas.services.rows``
    - ``truenas.vms.count_total``
    - ``truenas.vms.count_running``
    - ``truenas.vms.count_stopped``
    - ``truenas.vms.rows``
    - ``truenas.jails.count_total``
    - ``truenas.jails.count_running``
    - ``truenas.jails.count_stopped``
    - ``truenas.jails.rows``
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import os
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)

_OPTIONAL_ENDPOINTS = {
    "virt/instance",
    "jail",
    "update/check_available",
    "update/get_pending",
}


class _TrueNASAuthError(RuntimeError):
    """Raised when TrueNAS auth state is invalid."""


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
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return ""


def _strip_domain(hostname: str, enabled: bool) -> str:
    """Return short hostname when enabled and value looks like FQDN."""
    text = hostname.strip()
    if not enabled or not text:
        return text
    if "." not in text:
        return text
    return text.split(".", maxsplit=1)[0]


def _as_float(value: object, default: float = 0.0) -> float:
    """Coerce a scalar value to float."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _as_optional_float(value: object) -> float | None:
    """Coerce a scalar value to float, returning None when unavailable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
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


def _as_int(value: object, default: int = 0) -> int:
    """Coerce a scalar value to int."""
    if isinstance(value, int):
        return value
    if isinstance(value, (float, str)):
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return default
    return default


def _remote_ssl_context() -> ssl.SSLContext:
    """Return an SSL context for remote HTTP(S) URLs.

    If CASEDD_TRUENAS_VERIFY_SSL is set to 'false', returns
    an unverified context (skip hostname and cert checks).
    """
    verify = os.environ.get("CASEDD_TRUENAS_VERIFY_SSL", "true").lower() != "false"
    if not verify:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    return ssl.create_default_context()


@dataclass(frozen=True)
class _ApiCall:
    """One TrueNAS API request descriptor."""

    endpoint: str
    method: str = "GET"
    params: dict[str, object] | None = None


class TrueNASGetter(BaseGetter):
    """Getter for TrueNAS storage system information.

    Connects to TrueNAS via REST API and publishes system, pool,
    disk, and service telemetry.

    Args:
        store: Shared data store instance.
        interval: Poll interval in seconds (default: 10.0).
        host: TrueNAS hostname/IP (env: CASEDD_TRUENAS_HOST).
        port: TrueNAS HTTP(S) port (env: CASEDD_TRUENAS_PORT, def: 80).
        api_key: TrueNAS API key (env: CASEDD_TRUENAS_API_KEY).
    """

    def __init__(  # noqa: PLR0913 -- explicit config mapping is preferable
        self,
        store: DataStore,
        interval: float = 10.0,
        host: str | None = None,
        port: int | None = None,
        api_key: str | None = None,
        strip_domain_hostname: bool = True,
    ) -> None:
        """Initialise the TrueNAS getter.

        Args:
            store: The shared :class:`~casedd.data_store.DataStore`.
            interval: Seconds between each poll (default: 10.0).
            host: TrueNAS host/IP (or CASEDD_TRUENAS_HOST env var).
            port: TrueNAS port (or CASEDD_TRUENAS_PORT env var, default 80).
            api_key: TrueNAS API key (or CASEDD_TRUENAS_API_KEY env var).
        """
        super().__init__(store, interval)
        raw_host = host or os.environ.get("CASEDD_TRUENAS_HOST", "")
        self._port = port or _as_int(os.environ.get("CASEDD_TRUENAS_PORT"), 80)
        self._api_key = api_key or os.environ.get("CASEDD_TRUENAS_API_KEY", "")

        host_value = raw_host.strip()
        scheme = "https" if self._port == 443 else "http"
        if "://" in host_value:
            parsed = urlsplit(host_value)
            if parsed.scheme in {"http", "https"}:
                scheme = parsed.scheme
            host_value = parsed.netloc or parsed.path

        if "/" in host_value:
            host_value = host_value.rsplit("/", 1)[0]
        if host_value.count(":") == 1:
            maybe_host, maybe_port = host_value.rsplit(":", 1)
            if maybe_port.isdigit():
                host_value = maybe_host

        self._host = host_value
        self._base_url = f"{scheme}://{self._host}:{self._port}/api/v2.0"
        self._session_id: str | None = None
        self._strip_domain_hostname = strip_domain_hostname

    async def fetch(self) -> dict[str, StoreValue]:
        """Sample TrueNAS system state.

        Returns:
            Dict with ``truenas.*`` keys.
        """
        if not self._host or not self._api_key:
            _log.debug("TrueNAS getter disabled: missing host or api_key")
            return {"truenas.auth.ok": 0.0}

        try:
            return await asyncio.to_thread(self._sample)
        except _TrueNASAuthError:
            return {"truenas.auth.ok": 0.0}
        except Exception as exc:
            _log.warning("TrueNAS getter fetch error: %s", exc)
            return {"truenas.system.reachable": 0.0}

    def _sample(self) -> dict[str, StoreValue]:
        """Blocking TrueNAS data sample.

        Returns:
            Dict of store updates.
        """
        out: dict[str, StoreValue] = {
            "truenas.auth.ok": 0.0,
            "truenas.system.reachable": 0.0,
        }

        try:
            # Fetch system info
            system_info = self._call("system/info")
            if system_info:
                out["truenas.auth.ok"] = 1.0
                out["truenas.system.reachable"] = 1.0
                hostname = _as_text(_as_dict(system_info).get("hostname", ""))
                out["truenas.system.hostname"] = _strip_domain(
                    hostname,
                    self._strip_domain_hostname,
                )
                out["truenas.system.model"] = _as_text(
                    _as_dict(system_info).get("system", "")
                )
                out["truenas.system.version"] = _as_text(
                    _as_dict(system_info).get("version", "")
                )
                out["truenas.system.uptime"] = _as_float(
                    _as_dict(system_info).get("uptime", 0.0)
                )

            # Fetch pools, disks, users, and services
            self._sample_pools(out)
            self._sample_disks(out)
            self._sample_users(out)
            self._sample_services(out)
            self._sample_vms(out)
            self._sample_jails(out)
            self._sample_updates(out)
            self._sample_reporting(out)

        except Exception as exc:
            _log.warning("TrueNAS sample error: %s", exc)

        return out

    def _sample_pools(self, out: dict[str, StoreValue]) -> None:
        """Sample TrueNAS pool data.

        Args:
            out: Output dict to populate with pool entries.
        """
        pools = self._call("pool")
        datasets = self._call("pool/dataset")
        pool_list = _as_list(pools)
        dataset_list = _as_list(datasets)

        dataset_capacity: dict[str, tuple[int, int]] = {}
        for dataset in dataset_list:
            dataset_dict = _as_dict(dataset)
            pool_name = _as_text(dataset_dict.get("pool", ""))
            dataset_id = _as_text(dataset_dict.get("id", ""))
            # Root dataset id usually matches the pool name and carries pool totals.
            if not pool_name or dataset_id != pool_name:
                continue

            used_dict = _as_dict(dataset_dict.get("used", {}))
            available_dict = _as_dict(dataset_dict.get("available", {}))
            used_bytes = _as_int(used_dict.get("parsed", 0))
            available_bytes = _as_int(available_dict.get("parsed", 0))
            if used_bytes > 0 or available_bytes > 0:
                dataset_capacity[pool_name] = (used_bytes, available_bytes)

        pool_rows: list[str] = []
        for idx, pool in enumerate(pool_list[:10], 1):
            pool_dict = _as_dict(pool)
            pool_name = _as_text(pool_dict.get("name", ""))
            pool_status = _as_text(pool_dict.get("status", "unknown"))
            pool_stats = _as_dict(pool_dict.get("stats", {}))
            size_bytes = _as_int(pool_stats.get("size", 0))
            allocated_bytes = _as_int(pool_stats.get("allocated", 0))

            if size_bytes <= 0 and pool_name in dataset_capacity:
                used_bytes, available_bytes = dataset_capacity[pool_name]
                allocated_bytes = used_bytes
                size_bytes = used_bytes + available_bytes

            out[f"truenas.pool_{idx}.name"] = pool_name
            out[f"truenas.pool_{idx}.status"] = pool_status
            used_pct: float | None = None
            if size_bytes > 0:
                used_pct = round((allocated_bytes / size_bytes) * 100, 1)
                free_tb = round((size_bytes - allocated_bytes) / (1024**4), 2)
                total_tb = round(size_bytes / (1024**4), 2)
                out[f"truenas.pool_{idx}.used_percent"] = used_pct
                out[f"truenas.pool_{idx}.free_tb"] = free_tb
                out[f"truenas.pool_{idx}.total_tb"] = total_tb

            # Add to rows for table display (limit to 3 rows)
            if idx <= 3:
                status_icon = "●" if pool_status.strip().lower() == "healthy" else "▼"
                usage_text = f"{used_pct}%" if used_pct is not None else "--"
                row = f"{pool_name}|{status_icon}|{usage_text}"
                pool_rows.append(row)

        if pool_rows:
            out["truenas.pools.rows"] = "\n".join(pool_rows)

    def _sample_disks(self, out: dict[str, StoreValue]) -> None:
        """Sample TrueNAS disk data.

        Args:
            out: Output dict to populate with disk entries.
        """
        disks = self._call("disk")
        smart_results = self._call("smart/test/results")
        disk_list = _as_list(disks)
        smart_map = self._build_smart_status_map(_as_list(smart_results))

        disk_temp_graphs: list[dict[str, object]] = [
            {"name": "disktemp", "identifier": _as_text(_as_dict(disk).get("name", ""))}
            for disk in disk_list
            if _as_text(_as_dict(disk).get("name", ""))
        ]
        disk_temp_map = self._reporting_latest_values(disk_temp_graphs)

        disk_rows: list[str] = []
        for idx, disk in enumerate(disk_list[:20], 1):
            disk_dict = _as_dict(disk)
            disk_name = _as_text(disk_dict.get("name", ""))
            disk_status = _as_text(disk_dict.get("status", "unknown"))
            size_bytes = _as_int(disk_dict.get("size", 0))
            reported_temp = _as_optional_float(disk_dict.get("temperature"))
            sampled_temp = disk_temp_map.get(f"disktemp:{disk_name}")
            temp_c = sampled_temp if sampled_temp is not None else reported_temp

            out[f"truenas.disk_{idx}.name"] = disk_name
            out[f"truenas.disk_{idx}.status"] = disk_status
            if size_bytes > 0:
                out[f"truenas.disk_{idx}.size_tb"] = round(
                    size_bytes / (1024**4), 2
                )
            if temp_c is not None and temp_c >= 0:
                out[f"truenas.disk_{idx}.temp_c"] = round(temp_c, 1)

            smart_state = smart_map.get(disk_name, "UNK")
            level = "OK"
            if smart_state in {"FAIL", "ALERT"}:
                level = "ALERT"
            elif smart_state in {"WARN", "RUNNING"}:
                level = "WARN"
            elif smart_state == "UNK":
                level = "UNK"

            temp_text = "-"
            if temp_c is not None and temp_c >= 0:
                temp_text = f"{round(temp_c, 1):.1f}C"
            detail = temp_text
            row = f"{disk_name}|{level}|{detail}"
            disk_rows.append(row)

        if disk_rows:
            out["truenas.disks.rows"] = "\n".join(disk_rows[:10])

    def _build_smart_status_map(self, smart_rows: list[object]) -> dict[str, str]:
        """Build disk-name to SMART state mapping from smart/test/results payload."""
        out: dict[str, str] = {}
        for row_obj in smart_rows:
            row = _as_dict(row_obj)
            disk_name = _as_text(row.get("disk", ""))
            if not disk_name:
                continue

            current_test = _as_dict(row.get("current_test", {}))
            current_state = _as_text(current_test.get("status", ""))
            if current_state and current_state.upper() not in {"SUCCESS", "NONE"}:
                out[disk_name] = "RUNNING"
                continue

            tests = _as_list(row.get("tests", []))
            statuses = [
                _as_text(_as_dict(test_obj).get("status", "")).upper()
                for test_obj in tests
                if _as_text(_as_dict(test_obj).get("status", ""))
            ]
            if not statuses:
                out[disk_name] = "UNK"
                continue
            if any(status in {"FAILED", "FAIL", "ERROR", "ABORTED"} for status in statuses):
                out[disk_name] = "FAIL"
            elif any(status not in {"SUCCESS", "COMPLETED", "FINISHED"} for status in statuses):
                out[disk_name] = "WARN"
            else:
                out[disk_name] = "OK"
        return out

    def _sample_users(self, out: dict[str, StoreValue]) -> None:
        """Sample TrueNAS user count.

        Args:
            out: Output dict to populate with user count.
        """
        users = self._call("user")
        user_list = _as_list(users)
        out["truenas.users.count"] = len(
            [u for u in user_list if _as_dict(u).get("id") != 0]
        )

    def _sample_services(self, out: dict[str, StoreValue]) -> None:
        """Sample TrueNAS service data.

        Args:
            out: Output dict to populate with service rows.
        """
        services = self._call("service")
        service_list = _as_list(services)
        service_rows: list[str] = []
        for service in service_list:
            service_dict = _as_dict(service)
            service_name = _as_text(service_dict.get("service", ""))
            service_state = _as_text(service_dict.get("state", "unknown"))
            enabled_raw = service_dict.get("enable", False)
            is_enabled = bool(enabled_raw) if isinstance(enabled_raw, (bool, int)) else False
            if service_name:
                normalized = service_state.upper()
                is_running = normalized == "RUNNING"
                if not (is_running or is_enabled):
                    continue
                level = "OK"
                state_text = "RUN"
                if not is_running:
                    if is_enabled:
                        level = "ALERT"
                        state_text = "DOWN"
                    else:
                        level = "UNK"
                        state_text = "STOP"
                auto_text = "AUTO" if is_enabled else "MANUAL"
                row = f"{service_name}|{level}|{state_text} {auto_text}"
                service_rows.append(row)
        if service_rows:
            out["truenas.services.rows"] = "\n".join(service_rows[:50])

    def _sample_updates(self, out: dict[str, StoreValue]) -> None:
        """Sample TrueNAS update availability state."""
        available_payload = self._call("update/check_available")
        pending_payload = self._call("update/get_pending")

        status_text = "UP-TO-DATE"
        is_available = False
        pending_items = _as_list(pending_payload)

        if pending_items:
            is_available = True
            status_text = f"AVAILABLE ({len(pending_items)})"
        else:
            available_map = _as_dict(available_payload)
            status_raw = _as_text(available_map.get("status", "")).strip().upper()
            if status_raw in {"AVAILABLE", "PENDING", "REBOOT_REQUIRED"}:
                is_available = True
                status_text = status_raw.replace("_", " ")
            elif status_raw:
                status_text = status_raw.replace("_", " ")

        out["truenas.system.update_available"] = 1.0 if is_available else 0.0
        out["truenas.system.update_status"] = status_text

    def _sample_vms(self, out: dict[str, StoreValue]) -> None:
        """Sample TrueNAS virtual machine state (SCALE virt instances)."""
        vm_rows_raw = _as_list(self._call("virt/instance"))
        vm_rows: list[str] = []
        running = 0
        stopped = 0

        for vm_obj in vm_rows_raw:
            vm = _as_dict(vm_obj)
            name = _as_text(vm.get("name", "")) or _as_text(vm.get("id", ""))
            status_text = _as_text(vm.get("status", "")) or _as_text(vm.get("state", ""))
            state_upper = status_text.strip().upper()
            if state_upper in {"RUNNING", "ACTIVE", "UP", "STARTED"}:
                running += 1
                label = "Running"
            else:
                stopped += 1
                label = "Stopped"
            if name:
                vm_rows.append(f"{name}|{label}")

        out["truenas.vms.count_total"] = float(len(vm_rows))
        out["truenas.vms.count_running"] = float(running)
        out["truenas.vms.count_stopped"] = float(stopped)
        out["truenas.vms.rows"] = "\n".join(vm_rows[:20])

    def _sample_jails(self, out: dict[str, StoreValue]) -> None:
        """Sample TrueNAS jail state (CORE jail API)."""
        jail_rows_raw = _as_list(self._call("jail"))
        jail_rows: list[str] = []
        running = 0
        stopped = 0

        for jail_obj in jail_rows_raw:
            jail = _as_dict(jail_obj)
            name = (
                _as_text(jail.get("name", ""))
                or _as_text(jail.get("host_hostuuid", ""))
                or _as_text(jail.get("host_hostname", ""))
            )
            state_text = (
                _as_text(jail.get("state", ""))
                or _as_text(jail.get("status", ""))
                or _as_text(jail.get("running", ""))
            )
            state_upper = state_text.strip().upper()
            is_running = state_upper in {"UP", "RUNNING", "ON", "TRUE", "1"}
            if is_running:
                running += 1
                label = "Running"
            else:
                stopped += 1
                label = "Stopped"
            if name:
                jail_rows.append(f"{name}|{label}")

        out["truenas.jails.count_total"] = float(len(jail_rows))
        out["truenas.jails.count_running"] = float(running)
        out["truenas.jails.count_stopped"] = float(stopped)
        out["truenas.jails.rows"] = "\n".join(jail_rows[:20])

    def _sample_reporting(self, out: dict[str, StoreValue]) -> None:
        """Sample reporting graphs used for temperature telemetry."""
        graphs: list[dict[str, object]] = [{"name": "cputemp"}]
        values = self._reporting_latest_values(graphs)
        cpu_temp = values.get("cputemp")
        if cpu_temp is not None:
            out["truenas.performance.cpu_temp_c"] = round(cpu_temp, 1)

    def _reporting_latest_values(self, graphs: list[dict[str, object]]) -> dict[str, float]:
        """Fetch latest numeric value for each requested reporting graph."""
        if not graphs:
            return {}
        payload = {
            "graphs": graphs,
            "reporting_query": {"unit": "HOUR"},
        }
        response = self._call("reporting/get_data", method="POST", body=payload)
        rows = _as_list(response)
        out: dict[str, float] = {}
        for row_obj in rows:
            row = _as_dict(row_obj)
            name = _as_text(row.get("name", ""))
            identifier = _as_text(row.get("identifier", ""))
            key = f"{name}:{identifier}" if identifier else name
            latest = self._latest_graph_value(row)
            if latest is not None:
                out[key] = latest
        return out

    def _latest_graph_value(self, graph: dict[str, object]) -> float | None:
        """Return latest non-null reading from one reporting graph payload."""
        data = _as_list(graph.get("data", []))
        latest_values: list[float] = []
        for series_obj in data:
            series = _as_list(series_obj)
            for point in reversed(series):
                parsed = _as_optional_float(point)
                if parsed is not None:
                    latest_values.append(parsed)
                    break
        if not latest_values:
            return None
        return max(latest_values)

    def _call(self, endpoint: str, method: str = "GET", body: object | None = None) -> object:
        """Make a single TrueNAS API call.

        Args:
            endpoint: API endpoint path (e.g. "system/info").
            method: HTTP method (default: "GET").
            body: Optional JSON payload for POST-like calls.

        Returns:
            Parsed JSON response, or empty dict on error.

        Raises:
            _TrueNASAuthError: If auth fails.
        """
        url = f"{self._base_url}/{endpoint}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "User-Agent": "CASEDD/0.2",
        }
        request_body: bytes | None = None
        if body is not None:
            request_body = json.dumps(body).encode("utf-8")
        req = Request(url, data=request_body, headers=headers, method=method)  # noqa: S310
        ssl_context = _remote_ssl_context()

        try:
            with urlopen(req, timeout=5, context=ssl_context) as resp:  # noqa: S310
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except HTTPError as exc:
            if exc.code == 401:
                raise _TrueNASAuthError(f"TrueNAS auth failed: {exc}") from exc
            if endpoint in _OPTIONAL_ENDPOINTS and exc.code in {404, 405}:
                _log.debug("TrueNAS optional endpoint unavailable %s: %s", endpoint, exc)
                return {}
            _log.warning("TrueNAS API error on %s: %s", endpoint, exc)
            return {}
        except URLError as exc:
            _log.warning("TrueNAS connection error on %s: %s", endpoint, exc)
            return {}
