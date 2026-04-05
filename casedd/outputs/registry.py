"""Output backend registry and factory for CASEDD.

The registry maps backend type names to factory callables, enabling runtime
creation of :class:`~casedd.outputs.base.OutputBackend` instances from
declarative :class:`~casedd.config.OutputBackendConfig` settings.

Built-in types registered at module import:

=============  ============================================================
Type string    Backend class
=============  ============================================================
``framebuffer``  :class:`~casedd.outputs.framebuffer.FramebufferOutput`
``websocket``    :class:`~casedd.outputs.websocket.WebSocketOutput`
=============  ============================================================

Third-party extensions can register additional types at startup::

    from casedd.outputs.registry import get_default_registry
    from mypackage import MyBackend, my_factory

    get_default_registry().register("mytype", my_factory)

Public API:
    - :class:`OutputRegistry` — maps type names to factory callables
    - :func:`get_default_registry` — module-level singleton registry
"""

from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from casedd.outputs.base import OutputBackend

if TYPE_CHECKING:
    from casedd.config import Config, OutputBackendConfig

_log = logging.getLogger(__name__)

# Factory signature: (backend_config, global_daemon_config) → OutputBackend
_BackendFactory = Callable[["OutputBackendConfig", "Config"], OutputBackend]


class OutputRegistry:
    """Maps backend type names to factory callables.

    Backends are registered as callables that accept an
    :class:`~casedd.config.OutputBackendConfig` and the global
    :class:`~casedd.config.Config`, and return an initialised
    :class:`~casedd.outputs.base.OutputBackend` instance.

    The registry is *not* thread-safe; register all types at startup before
    the daemon's async event loop begins.
    """

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._factories: dict[str, _BackendFactory] = {}

    def register(self, type_name: str, factory: _BackendFactory) -> None:
        """Register a factory for a backend type.

        Args:
            type_name: Lowercase type identifier (e.g. ``"framebuffer"``).
            factory: Callable that creates an :class:`OutputBackend` from
                an :class:`~casedd.config.OutputBackendConfig` and the
                global :class:`~casedd.config.Config`.

        Raises:
            ValueError: If ``type_name`` is empty or already registered.
        """
        if not type_name:
            msg = "type_name must be a non-empty string"
            raise ValueError(msg)
        if type_name in self._factories:
            _log.warning("Overwriting existing backend factory for type %r", type_name)
        self._factories[type_name] = factory

    def create(
        self,
        cfg: OutputBackendConfig,
        global_cfg: Config,
    ) -> OutputBackend:
        """Instantiate a backend from its configuration.

        Args:
            cfg: Per-backend configuration block from ``casedd.yaml``.
            global_cfg: Global daemon configuration (supplies defaults for
                omitted per-backend settings).

        Returns:
            A ready-but-not-yet-started :class:`OutputBackend` instance.

        Raises:
            KeyError: If ``cfg.type`` is not registered.
        """
        factory = self._factories.get(cfg.type)
        if factory is None:
            known = ", ".join(sorted(self._factories))
            msg = (
                f"Unknown backend type {cfg.type!r}. "
                f"Registered types: {known or '(none)'}"
            )
            raise KeyError(msg)
        backend = factory(cfg, global_cfg)
        _log.debug(
            "Created backend %r of type %r: %s",
            getattr(cfg, "name", ""),
            cfg.type,
            backend,
        )
        return backend

    @property
    def registered_types(self) -> list[str]:
        """Sorted list of all registered backend type names."""
        return sorted(self._factories)


# ---------------------------------------------------------------------------
# Module-level default registry (populated at import time)
# ---------------------------------------------------------------------------

_default_registry: OutputRegistry | None = None


def get_default_registry() -> OutputRegistry:
    """Return (and lazily populate) the module-level default registry.

    Built-in ``framebuffer`` and ``websocket`` factories are registered on
    first call.  Subsequent calls return the same instance.

    Returns:
        The singleton :class:`OutputRegistry` with built-in types registered.
    """
    global _default_registry  # noqa: PLW0603 - intentional module singleton
    if _default_registry is not None:
        return _default_registry

    _default_registry = OutputRegistry()
    _register_builtin_backends(_default_registry)
    return _default_registry


def _register_builtin_backends(registry: OutputRegistry) -> None:
    """Register the built-in framebuffer and websocket backend factories.

    Imports are deferred inside this function to avoid circular imports;
    both concrete backend modules import from ``base`` but not from
    ``registry``.

    Args:
        registry: Target registry to populate.
    """
    from casedd.outputs.framebuffer import (  # noqa: PLC0415 - deferred to avoid circular import
        FramebufferOutput,
    )
    from casedd.outputs.websocket import (  # noqa: PLC0415 - deferred to avoid circular import
        WebSocketOutput,
    )

    _bind_host = "0.0.0.0"  # noqa: S104  # string literal, not a bind call

    def _framebuffer_factory(
        cfg: OutputBackendConfig,
        global_cfg: Config,
    ) -> OutputBackend:
        """Create a :class:`FramebufferOutput` from a backend config block."""
        device = cfg.device if cfg.device is not None else global_cfg.fb_device
        no_fb = not cfg.enabled or global_cfg.no_fb
        rot = cfg.rotation if cfg.rotation is not None else global_cfg.fb_rotation
        return FramebufferOutput(Path(str(device)), disabled=no_fb, rotation=rot)

    def _websocket_factory(
        cfg: OutputBackendConfig,
        global_cfg: Config,
    ) -> OutputBackend:
        """Create a :class:`WebSocketOutput` from a backend config block."""
        port = cfg.port if cfg.port is not None else global_cfg.ws_port
        return WebSocketOutput(_bind_host, port)

    registry.register("framebuffer", _framebuffer_factory)
    registry.register("websocket", _websocket_factory)
