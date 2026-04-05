"""Abstract base class for all CASEDD output backends.

Every concrete output (framebuffer, WebSocket, future cast/HDMI backends)
must subclass :class:`OutputBackend` and implement the three abstract methods.

The interface is deliberately minimal: the render loop calls :meth:`output`
once per rendered frame; lifecycle is managed via :meth:`start` /
:meth:`stop`.

Public API:
    - :class:`OutputBackend` — abstract base class for output sinks
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from PIL import Image


class OutputBackend(ABC):
    """Abstract base class for a CASEDD output backend.

    Concrete implementations write rendered frames to a specific sink —
    a Linux framebuffer device, a WebSocket server, an HDMI output, etc.
    All backends share a single data-collection pipeline (``DataStore``) so
    there is no redundant polling regardless of how many outputs are active.

    Lifecycle::

        backend = SomeBackend(config)
        await backend.start()
        # ... render loop:
        await backend.output(image)
        # ... shutdown:
        await backend.stop()
    """

    @abstractmethod
    async def start(self) -> None:
        """Open connections, claim devices, start background tasks.

        Called once at daemon startup before the first render tick.
        Implementations should be idempotent (safe to call multiple times).
        """

    @abstractmethod
    async def stop(self) -> None:
        """Release resources, close connections, stop background tasks.

        Called once at daemon shutdown after the last render tick.
        Implementations should be idempotent (safe to call multiple times).
        """

    @abstractmethod
    async def output(self, image: Image.Image) -> None:
        """Deliver one rendered frame to this backend's sink.

        The image is already encoded at the backend's configured resolution.
        Implementations that need a different resolution should resize *in
        place* before writing.  Blocking I/O must use ``asyncio.to_thread``
        to avoid stalling the event loop.

        Args:
            image: The fully-rendered ``PIL.Image.Image`` in ``RGB`` mode.
        """

    def is_healthy(self) -> bool:
        """Return ``True`` when the backend is operating normally.

        Override to expose device-level health (e.g. device file accessible,
        WebSocket server responding).  The default implementation always
        returns ``True``.

        Returns:
            ``True`` if healthy, ``False`` if degraded or unavailable.
        """
        return True

    def get_config(self) -> dict[str, object]:
        """Return a snapshot of the backend's current configuration.

        Used for debugging, logging, and the ``/api/health`` endpoint.
        The default implementation returns an empty dict; override to expose
        relevant settings.

        Returns:
            Mapping of configuration key names to their current values.
        """
        return {}
