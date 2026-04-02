"""Abstract base class for all CASEDD data-source getters.

Each getter polls a data source on a fixed interval and pushes key/value
pairs into the shared :class:`~casedd.data_store.DataStore`.

Public API:
    - :class:`BaseGetter` — subclass this to add a new data source.
"""

from abc import ABC, abstractmethod
import asyncio
import logging

from casedd.data_store import DataStore, StoreValue
from casedd.getter_health import GetterHealthRegistry

_log = logging.getLogger(__name__)


class BaseGetter(ABC):
    """Async polling loop for a single data source.

    Subclasses implement :meth:`fetch` to perform one sample and return a
    dict of store updates. The base class handles the polling interval,
    error isolation, writing to the store, and optional health tracking.

    Args:
        store: Shared data store instance.
        interval: Poll interval in seconds. Defaults to 2.0.
    """

    def __init__(self, store: DataStore, interval: float = 2.0) -> None:
        """Initialise the getter.

        Args:
            store: The shared :class:`~casedd.data_store.DataStore`.
            interval: Seconds between each poll (default: 2.0).
        """
        self._store = store
        self._interval = interval
        self._running = False
        self._health: GetterHealthRegistry | None = None

    def attach_health(self, registry: GetterHealthRegistry) -> None:
        """Wire a health registry for observability after construction.

        Call this once before :meth:`run` is started to enable health tracking.
        The getter will be registered immediately so the registry knows about it
        even before the first successful fetch.

        Args:
            registry: Health registry to record fetch outcomes into.
        """
        self._health = registry
        registry.register(type(self).__name__)

    @abstractmethod
    async def fetch(self) -> dict[str, StoreValue]:
        """Perform one data sample.

        Returns:
            A dict of dotted key → value pairs to write into the data store.
            An empty dict is valid (no-op for this cycle).
        """
        ...

    async def run(self) -> None:
        """Poll indefinitely, sleeping ``interval`` seconds between each fetch.

        Errors from :meth:`fetch` are logged at WARNING level and the loop
        continues — a single bad sample must never crash the getter.
        Health outcomes are reported to the registry when one is attached.
        Repeated errors are rate-limited to avoid log spam (one log per minute).
        """
        self._running = True
        name = type(self).__name__
        _log.info("Getter started: %s (interval=%.1fs)", name, self._interval)

        # Mark the getter as actually starting (it may have been registered as
        # "inactive" when not scheduled). This lets the health snapshot show
        # precise state for UI/alerts.
        if self._health is not None:
            self._health.mark_starting(name)

        while self._running:
            try:
                data = await self.fetch()
                if data:
                    self._store.update(data)
                if self._health is not None:
                    self._health.record_success(name)
            except Exception as exc:
                should_log = True
                if self._health is not None:
                    should_log = self._health.record_error(name, str(exc))
                if should_log:
                    _log.warning("Getter %s raised an exception:", name, exc_info=True)

            await asyncio.sleep(self._interval)

    def stop(self) -> None:
        """Signal the polling loop to exit after its current sleep."""
        self._running = False

    @property
    def interval_seconds(self) -> float:
        """Return the configured poll interval in seconds."""
        return self._interval
