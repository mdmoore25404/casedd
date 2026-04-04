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
    assert out["truenas.pools.rows"] == "storagepool|⚠|80.0%"
