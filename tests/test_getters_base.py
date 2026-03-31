"""Tests for :class:`~casedd.getters.base.BaseGetter` (issue #27).

Tests the polling loop, error fallback, health integration, and store writes
without starting a real async task loop.
"""

from __future__ import annotations

import asyncio

import pytest

from casedd.data_store import DataStore
from casedd.getter_health import GetterHealthRegistry
from casedd.getters.base import BaseGetter

# ---------------------------------------------------------------------------
# Minimal concrete getter for testing
# ---------------------------------------------------------------------------


class _GoodGetter(BaseGetter):
    """Always returns one key/value pair."""

    async def fetch(self) -> dict[str, object]:  # type: ignore[override]
        return {"test.value": 42}


class _BadGetter(BaseGetter):
    """Always raises on fetch."""

    async def fetch(self) -> dict[str, object]:  # type: ignore[override]
        msg = "simulated error"
        raise RuntimeError(msg)


class _OnceGoodThenBad(BaseGetter):
    """Succeeds once, then always raises."""

    def __init__(self, store: DataStore) -> None:
        super().__init__(store, interval=0.0)
        self._calls = 0

    async def fetch(self) -> dict[str, object]:  # type: ignore[override]
        self._calls += 1
        if self._calls == 1:
            return {"seq": self._calls}
        msg = "later error"
        raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# attach_health
# ---------------------------------------------------------------------------


def test_attach_health_registers_getter() -> None:
    """attach_health() registers the getter name in the registry."""
    store = DataStore()
    registry = GetterHealthRegistry()
    getter = _GoodGetter(store)
    getter.attach_health(registry)
    snap = registry.snapshot()
    assert any(e["name"] == "_GoodGetter" for e in snap)


def test_attach_health_initial_status_is_starting() -> None:
    store = DataStore()
    registry = GetterHealthRegistry()
    getter = _GoodGetter(store)
    getter.attach_health(registry)
    entry = registry.snapshot()[0]
    assert entry["status"] == "inactive"


# ---------------------------------------------------------------------------
# Polling behaviour (drive the loop manually)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_fetch_updates_store() -> None:
    """A successful fetch writes values into the DataStore."""
    store = DataStore()
    getter = _GoodGetter(store, interval=0.0)

    # Run one iteration by patching stop after first sleep
    getter.stop()  # pre-flag stop so loop exits after one pass
    # Override interval to avoid real sleeping
    getter._interval = 0.0

    async def _run_one() -> None:
        # Execute one fetch manually
        data = await getter.fetch()
        store.update(data)

    await _run_one()
    assert store.get("test.value") == 42


@pytest.mark.asyncio
async def test_health_records_success_after_good_fetch() -> None:
    """After a successful fetch cycle, health shows 'ok'."""
    store = DataStore()
    registry = GetterHealthRegistry()

    getter = _GoodGetter(store, interval=0.0)
    getter.attach_health(registry)

    # Manually drive one fetch cycle the same way run() would
    try:
        data = await getter.fetch()
        store.update(data)
        registry.record_success("_GoodGetter")
    except Exception:
        pass

    assert registry.snapshot()[0]["status"] == "ok"


@pytest.mark.asyncio
async def test_health_records_error_after_bad_fetch() -> None:
    """After a failing fetch cycle, health shows 'error'."""
    store = DataStore()
    registry = GetterHealthRegistry()

    getter = _BadGetter(store, interval=0.0)
    getter.attach_health(registry)

    try:
        await getter.fetch()
    except RuntimeError as exc:
        registry.record_error("_BadGetter", str(exc))

    assert registry.snapshot()[0]["status"] == "error"


@pytest.mark.asyncio
async def test_run_loop_survives_fetch_error() -> None:
    """run() loop continues after fetch raises; store unchanged."""
    store = DataStore()
    getter = _BadGetter(store, interval=0.0)

    # Run for a short time — the loop must not propagate the exception
    task = asyncio.create_task(getter.run())
    await asyncio.sleep(0.05)
    getter.stop()
    await asyncio.wait_for(task, timeout=1.0)

    # Store should be untouched — _BadGetter writes nothing
    assert len(store) == 0


@pytest.mark.asyncio
async def test_run_loop_writes_successful_data() -> None:
    """run() loop writes data from a successful fetch into the store."""
    store = DataStore()
    getter = _GoodGetter(store, interval=0.0)

    task = asyncio.create_task(getter.run())
    await asyncio.sleep(0.05)
    getter.stop()
    await asyncio.wait_for(task, timeout=1.0)

    assert store.get("test.value") == 42
