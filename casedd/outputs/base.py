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
from typing import TYPE_CHECKING

from PIL import Image

if TYPE_CHECKING:
    from casedd.config import OutputBackendConfig


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
    async def output(
        self,
        image: Image.Image,
        config: OutputBackendConfig | None = None,
    ) -> None:
        """Deliver one rendered frame to this backend's sink.

        The image has already been rendered at the primary panel's resolution.
        Backends that declare a different ``width``/``height`` in their
        :class:`~casedd.config.OutputBackendConfig` will receive a pre-scaled
        copy via :func:`~casedd.outputs.base.scale_for_backend` before this
        method is called by the daemon's ``_dispatch_frame`` path.

        The ``config`` parameter carries the backend's own
        :class:`~casedd.config.OutputBackendConfig` entry (or ``None`` for
        the legacy direct-call path) so implementations may inspect it for
        format, quality, or other backend-specific hints.

        Blocking I/O must use ``asyncio.to_thread`` to avoid stalling the
        event loop.

        Args:
            image: The fully-rendered ``PIL.Image.Image`` in ``RGB`` mode,
                already scaled to this backend's declared resolution.
            config: The backend's own ``OutputBackendConfig``, or ``None``
                when called without a config context (e.g. in tests).
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


def scale_for_backend(
    image: Image.Image,
    config: OutputBackendConfig | None,
) -> Image.Image:
    """Return *image* scaled to the backend's declared resolution.

    If ``config`` is ``None`` or neither ``width`` nor ``height`` is set,
    the original image is returned unchanged (no copy).  When only one
    dimension is set, proportional scaling is applied.  When both are set
    the image is scaled to fit within the declared box using
    ``Image.LANCZOS`` resampling, preserving aspect ratio with letterboxing
    only if the aspect ratios differ materially.

    This helper is called by the daemon's ``_dispatch_frame`` path before
    invoking :meth:`OutputBackend.output` so backends always receive an
    image at their configured resolution.

    Args:
        image: Source rendered frame in ``RGB`` mode.
        config: Optional backend config carrying ``width``/``height`` hints.

    Returns:
        A scaled ``PIL.Image.Image``, or the original if no resize needed.
    """
    if config is None:
        return image
    target_w = config.width
    target_h = config.height
    if target_w is None and target_h is None:
        return image
    src_w, src_h = image.size
    if target_w is None:
        # Scale proportionally by height.
        assert target_h is not None
        target_w = max(1, int(src_w * target_h / src_h))
    elif target_h is None:
        target_h = max(1, int(src_h * target_w / src_w))
    if (target_w, target_h) == (src_w, src_h):
        return image
    return image.resize((target_w, target_h), Image.Resampling.LANCZOS)
