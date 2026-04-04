"""Unit tests for the TrueNAS getter."""

from casedd.data_store import DataStore
from casedd.getters.truenas import TrueNASGetter


def test_truenas_getter_disabled_when_missing_host() -> None:
    """TrueNAS getter should disable itself when host is not configured."""
    store = DataStore()
    getter = TrueNASGetter(
        store,
        interval=1.0,
        host="",
        api_key="key",
    )
    data = getter._sample()
    assert data["truenas.auth.ok"] == 0.0


def test_truenas_getter_disabled_when_missing_api_key() -> None:
    """TrueNAS getter should disable itself when API key is not configured."""
    store = DataStore()
    getter = TrueNASGetter(
        store,
        interval=1.0,
        host="nas.local",
        api_key="",
    )
    data = getter._sample()
    assert data["truenas.auth.ok"] == 0.0


def test_truenas_getter_returns_auth_and_reachable_keys() -> None:
    """TrueNAS getter should always return auth and reachable keys."""
    store = DataStore()
    getter = TrueNASGetter(
        store,
        interval=1.0,
        host="nas.local",
        api_key="test-key",
    )
    data = getter._sample()
    assert "truenas.auth.ok" in data
    assert "truenas.system.reachable" in data


def test_truenas_getter_uses_https_on_port_443() -> None:
    """Port 443 should produce an HTTPS base URL."""
    store = DataStore()
    getter = TrueNASGetter(
        store,
        interval=1.0,
        host="nas.local",
        port=443,
        api_key="test-key",
    )
    assert getter._base_url.startswith("https://")


def test_truenas_pool_usage_falls_back_to_dataset_values() -> None:
    """Pool usage keys should be filled from pool/dataset when pool stats are empty."""
    store = DataStore()
    getter = TrueNASGetter(
        store,
        interval=1.0,
        host="nas.local",
        api_key="test-key",
    )

    def _fake_call(endpoint: str, method: str = "GET") -> object:
        _ = method
        if endpoint == "pool":
            return [{"name": "storagepool", "status": "DEGRADED", "stats": {}}]
        if endpoint == "pool/dataset":
            return [
                {
                    "id": "storagepool",
                    "pool": "storagepool",
                    "used": {"parsed": 8 * 1024**4},
                    "available": {"parsed": 2 * 1024**4},
                }
            ]
        return []

    getter._call = _fake_call  # type: ignore[method-assign]
    out: dict[str, float | int | str] = {}
    getter._sample_pools(out)

    assert out["truenas.pool_1.name"] == "storagepool"
    assert out["truenas.pool_1.status"] == "DEGRADED"
    assert out["truenas.pool_1.used_percent"] == 80.0
    assert out["truenas.pool_1.free_tb"] == 2.0
    assert out["truenas.pools.rows"] == "storagepool|▼|80.0%"


def test_truenas_services_rows_encode_levels_for_coloring() -> None:
    """Service rows should encode OK/UNK/ALERT levels for renderer color mapping."""
    getter = TrueNASGetter(DataStore(), host="nas.local", api_key="key")

    def _fake_call(endpoint: str, method: str = "GET", body: object | None = None) -> object:
        _ = method
        _ = body
        if endpoint == "service":
            return [
                {"service": "ssh", "state": "RUNNING", "enable": True},
                {"service": "ftp", "state": "STOPPED", "enable": False},
                {"service": "iscsitarget", "state": "STOPPED", "enable": True},
                {"service": "nfs", "state": "RUNNING", "enable": False},
            ]
        return []

    getter._call = _fake_call  # type: ignore[method-assign]
    out: dict[str, float | int | str] = {}
    getter._sample_services(out)

    assert out["truenas.services.rows"] == (
        "ssh|OK|RUN AUTO\n"
        "iscsitarget|ALERT|DOWN AUTO\n"
        "nfs|OK|RUN MANUAL"
    )


def test_truenas_disks_rows_include_temperature_detail() -> None:
    """Disk rows should expose temperature detail used for temp-scale coloring."""
    getter = TrueNASGetter(DataStore(), host="nas.local", api_key="key")

    def _fake_call(endpoint: str, method: str = "GET", body: object | None = None) -> object:
        _ = method
        if endpoint == "disk":
            return [
                {
                    "name": "ada0",
                    "status": "unknown",
                    "pool": "tank",
                    "size": 2 * 1024**4,
                }
            ]
        if endpoint == "smart/test/results":
            return [
                {
                    "disk": "ada0",
                    "tests": [{"status": "SUCCESS"}],
                    "current_test": {},
                }
            ]
        if endpoint == "reporting/get_data" and isinstance(body, dict):
            return [
                {
                    "name": "disktemp",
                    "identifier": "ada0",
                    "data": [[35.0]],
                }
            ]
        return []

    getter._call = _fake_call  # type: ignore[method-assign]
    out: dict[str, float | int | str] = {}
    getter._sample_disks(out)

    assert out["truenas.disk_1.name"] == "ada0"
    assert out["truenas.disk_1.size_tb"] == 2.0
    assert out["truenas.disk_1.temp_c"] == 35.0
    assert out["truenas.disks.rows"] == "ada0|OK|35.0C"


def test_truenas_updates_status_marks_available_when_pending() -> None:
    """Update state should report AVAILABLE when pending updates exist."""
    getter = TrueNASGetter(DataStore(), host="nas.local", api_key="key")

    def _fake_call(endpoint: str, method: str = "GET", body: object | None = None) -> object:
        _ = method
        _ = body
        if endpoint == "update/get_pending":
            return [{"name": "TrueNAS-24.10"}]
        if endpoint == "update/check_available":
            return {"status": "AVAILABLE"}
        return []

    getter._call = _fake_call  # type: ignore[method-assign]
    out: dict[str, float | int | str] = {}
    getter._sample_updates(out)

    assert out["truenas.system.update_available"] == 1.0
    assert out["truenas.system.update_status"] == "AVAILABLE (1)"


def test_truenas_vm_and_jail_rows_are_published() -> None:
    """VM and jail endpoints should publish count and table rows."""
    getter = TrueNASGetter(DataStore(), host="nas.local", api_key="key")

    def _fake_call(endpoint: str, method: str = "GET", body: object | None = None) -> object:
        _ = method
        _ = body
        if endpoint == "virt/instance":
            return [
                {"name": "vm-a", "status": "RUNNING"},
                {"name": "vm-b", "status": "STOPPED"},
            ]
        if endpoint == "jail":
            return [
                {"name": "jail-a", "state": "up"},
                {"name": "jail-b", "state": "down"},
            ]
        return []

    getter._call = _fake_call  # type: ignore[method-assign]
    out: dict[str, float | int | str] = {}
    getter._sample_vms(out)
    getter._sample_jails(out)

    assert out["truenas.vms.count_total"] == 2.0
    assert out["truenas.vms.count_running"] == 1.0
    assert out["truenas.vms.count_stopped"] == 1.0
    assert out["truenas.vms.rows"] == "vm-a|Running\nvm-b|Stopped"

    assert out["truenas.jails.count_total"] == 2.0
    assert out["truenas.jails.count_running"] == 1.0
    assert out["truenas.jails.count_stopped"] == 1.0
    assert out["truenas.jails.rows"] == "jail-a|Running\njail-b|Stopped"


def test_truenas_hostname_strips_domain_by_default() -> None:
    """FQDN hostnames should publish the short hostname by default."""
    getter = TrueNASGetter(DataStore(), host="nas.local", api_key="key")

    def _fake_call(endpoint: str, method: str = "GET", body: object | None = None) -> object:
        _ = method
        _ = body
        if endpoint == "system/info":
            return {
                "hostname": "nas1.example.local",
                "system": "TRUENAS",
                "version": "1.0",
                "uptime": 1,
            }
        return []

    getter._call = _fake_call  # type: ignore[method-assign]
    out = getter._sample()
    assert out["truenas.system.hostname"] == "nas1"


def test_truenas_hostname_keeps_domain_when_disabled() -> None:
    """Domain stripping should be optional for environments needing FQDN."""
    getter = TrueNASGetter(
        DataStore(),
        host="nas.local",
        api_key="key",
        strip_domain_hostname=False,
    )

    def _fake_call(endpoint: str, method: str = "GET", body: object | None = None) -> object:
        _ = method
        _ = body
        if endpoint == "system/info":
            return {
                "hostname": "nas1.example.local",
                "system": "TRUENAS",
                "version": "1.0",
                "uptime": 1,
            }
        return []

    getter._call = _fake_call  # type: ignore[method-assign]
    out = getter._sample()
    assert out["truenas.system.hostname"] == "nas1.example.local"
