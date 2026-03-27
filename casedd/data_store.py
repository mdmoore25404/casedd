"""Thread-safe in-RAM data store for CASEDD.

All data sources (getters, Unix socket, REST) write into this store.
The renderer reads from it on each frame. Values are Python primitives
(float, int, str) keyed by dotted namespace strings (e.g. ``cpu.temperature``).

The store uses a single ``threading.Lock`` for all reads and writes — contention
is negligible because writes are short assignments and renders read a snapshot.

Public API:
    - :class:`DataStore` — the store class; a singleton is held by the daemon.
"""

import threading
from typing import overload

# Type alias for the values held in the store
StoreValue = float | int | str


class DataStore:
    """Thread-safe key/value store for live display data.

    Keys use dotted namespaces (``cpu.temperature``, ``memory.percent``, …).
    Values are Python primitives: ``float``, ``int``, or ``str``.

    All public methods are safe to call from any thread or async task.
    """

    def __init__(self) -> None:
        """Initialise an empty store."""
        self._lock = threading.Lock()
        self._data: dict[str, StoreValue] = {}

    def set(self, key: str, value: StoreValue) -> None:
        """Write a single value into the store.

        Args:
            key: Dotted namespace key (e.g. ``cpu.temperature``).
            value: The value to store.
        """
        with self._lock:
            self._data[key] = value

    def update(self, mapping: dict[str, StoreValue]) -> None:
        """Atomically write multiple values into the store.

        Prefer this over calling :meth:`set` in a loop to minimise lock
        acquisitions.

        Args:
            mapping: Dict of key → value pairs to merge into the store.
        """
        with self._lock:
            self._data.update(mapping)

    @overload
    def get(self, key: str) -> StoreValue | None: ...

    @overload
    def get(self, key: str, default: StoreValue) -> StoreValue: ...

    def get(self, key: str, default: StoreValue | None = None) -> StoreValue | None:
        """Read a value from the store.

        Args:
            key: Dotted namespace key.
            default: Value returned when the key is absent (default: ``None``).

        Returns:
            The stored value, or ``default`` if the key does not exist.
        """
        with self._lock:
            return self._data.get(key, default)

    def snapshot(self) -> dict[str, StoreValue]:
        """Return a shallow copy of the entire store.

        The copy is safe to read outside the lock — it reflects the state at
        the moment the snapshot was taken.

        Returns:
            Dict of all key → value pairs currently in the store.
        """
        with self._lock:
            return dict(self._data)

    def keys(self) -> list[str]:
        """Return a sorted list of all keys currently in the store.

        Returns:
            Sorted list of key strings.
        """
        with self._lock:
            return sorted(self._data.keys())

    def __len__(self) -> int:
        """Return the number of keys in the store."""
        with self._lock:
            return len(self._data)
