"""Tests for :class:`~casedd.data_store.DataStore`."""

from __future__ import annotations

import threading

from casedd.data_store import DataStore


def test_set_and_get() -> None:
    """get() returns a previously set value."""
    store = DataStore()
    store.set("cpu.temperature", 72.5)
    assert store.get("cpu.temperature") == 72.5


def test_get_missing_returns_none() -> None:
    """get() returns None for unknown keys (no default)."""
    store = DataStore()
    assert store.get("no.such.key") is None


def test_get_default() -> None:
    """get() returns the explicit default for unknown keys."""
    store = DataStore()
    assert store.get("missing", 0.0) == 0.0


def test_update_multiple_keys() -> None:
    """update() writes all supplied key/value pairs atomically."""
    store = DataStore()
    store.update({"a": 1, "b": 2.5, "c": "hello"})
    assert store.get("a") == 1
    assert store.get("b") == 2.5
    assert store.get("c") == "hello"


def test_overwrite() -> None:
    """A second set() call overwrites the previous value."""
    store = DataStore()
    store.set("x", 1)
    store.set("x", 99)
    assert store.get("x") == 99


def test_snapshot_is_independent() -> None:
    """Mutations after snapshot() do not affect the snapshot."""
    store = DataStore()
    store.set("k", 1)
    snap = store.snapshot()
    store.set("k", 2)
    assert snap["k"] == 1


def test_keys_sorted() -> None:
    """keys() returns a sorted list of all current keys."""
    store = DataStore()
    store.update({"z": 1, "a": 2, "m": 3})
    assert store.keys() == ["a", "m", "z"]


def test_len() -> None:
    """len() returns the number of stored keys."""
    store = DataStore()
    assert len(store) == 0
    store.set("k", 1)
    assert len(store) == 1


def test_thread_safe_concurrent_writes() -> None:
    """Concurrent writes from many threads do not raise or corrupt state."""
    store = DataStore()
    errors: list[Exception] = []

    def writer(n: int) -> None:
        try:
            for i in range(100):
                store.set(f"t{n}.{i}", i)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(store) == 10 * 100
