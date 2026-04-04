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
