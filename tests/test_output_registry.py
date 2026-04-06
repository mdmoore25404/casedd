"""Unit tests for the pluggable output backend registry and concrete backends.

Covers:
- :class:`~casedd.outputs.base.OutputBackend` abstract interface
- :class:`~casedd.outputs.registry.OutputRegistry` factory pattern
- :class:`~casedd.outputs.framebuffer.FramebufferOutput` as OutputBackend
- :class:`~casedd.outputs.websocket.WebSocketOutput` as OutputBackend
- Config model :class:`~casedd.config.OutputBackendConfig` parsing
- Registry integration without real devices or network sockets
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from PIL import Image
import pytest

from casedd.config import OutputBackendConfig
from casedd.outputs.base import OutputBackend, scale_for_backend
from casedd.outputs.framebuffer import FramebufferOutput
from casedd.outputs.registry import OutputRegistry, get_default_registry
from casedd.outputs.websocket import WebSocketOutput

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ConcreteBackend(OutputBackend):
    """Minimal concrete backend used as a test double."""

    def __init__(self) -> None:
        """Initialise tracking state."""
        self.started = False
        self.stopped = False
        self.received: list[Image.Image] = []

    async def start(self) -> None:
        """Mark as started."""
        self.started = True

    async def stop(self) -> None:
        """Mark as stopped."""
        self.stopped = True

    async def output(
        self,
        image: Image.Image,
        config: OutputBackendConfig | None = None,
    ) -> None:
        """Record received images."""
        self.received.append(image)

    def is_healthy(self) -> bool:
        """Return True while started and not stopped."""
        return self.started and not self.stopped

    def get_config(self) -> dict[str, object]:
        """Return dummy config snapshot."""
        return {"type": "test", "started": self.started}


def _make_cfg(**kwargs: Any) -> OutputBackendConfig:
    """Create an :class:`OutputBackendConfig` with permissive defaults.

    Args:
        **kwargs: Field overrides for the model.

    Returns:
        Validated :class:`OutputBackendConfig` instance.
    """
    data: dict[str, Any] = {"type": "test", "enabled": True}
    data.update(kwargs)
    return OutputBackendConfig.model_validate(data)


# ---------------------------------------------------------------------------
# OutputBackend ABC contract
# ---------------------------------------------------------------------------


def test_outputbackend_cannot_instantiate() -> None:
    """ABC raises TypeError when instantiated directly."""
    with pytest.raises(TypeError):
        OutputBackend()  # type: ignore[abstract]


def test_concrete_backend_default_healthy() -> None:
    """Concrete backend inherits default ``is_healthy()`` = True."""

    class _SimpleBackend(OutputBackend):
        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

        async def output(self, image: Image.Image) -> None:
            pass

    backend = _SimpleBackend()
    assert backend.is_healthy() is True


def test_concrete_backend_default_get_config() -> None:
    """Default :meth:`~casedd.outputs.base.OutputBackend.get_config` returns empty dict."""

    class _SimpleBackend(OutputBackend):
        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

        async def output(self, image: Image.Image) -> None:
            pass

    backend = _SimpleBackend()
    assert backend.get_config() == {}


# ---------------------------------------------------------------------------
# OutputRegistry
# ---------------------------------------------------------------------------


def test_registry_register_and_create() -> None:
    """Factory registered under a type name is used by create()."""
    reg = OutputRegistry()
    backend_instance = _ConcreteBackend()

    def _factory(
        cfg: OutputBackendConfig,
        global_cfg: object,  # type: ignore[override]
    ) -> OutputBackend:
        return backend_instance

    reg.register("test", _factory)
    cfg = _make_cfg(type="test")
    result = reg.create(cfg, MagicMock())  # type: ignore[arg-type]

    assert result is backend_instance


def test_registry_registered_types() -> None:
    """``registered_types`` reflects what has been registered."""
    reg = OutputRegistry()
    assert reg.registered_types == []

    reg.register("alpha", lambda cfg, gcfg: _ConcreteBackend())  # type: ignore[arg-type]
    reg.register("beta", lambda cfg, gcfg: _ConcreteBackend())  # type: ignore[arg-type]

    assert reg.registered_types == ["alpha", "beta"]


def test_registry_unknown_type_raises() -> None:
    """Creating an unregistered type raises KeyError."""
    reg = OutputRegistry()
    cfg = _make_cfg(type="nonexistent")
    with pytest.raises(KeyError, match="nonexistent"):
        reg.create(cfg, MagicMock())  # type: ignore[arg-type]


def test_registry_overwrite_warns(caplog: pytest.LogCaptureFixture) -> None:
    """Overwriting a registered type emits a WARNING."""
    reg = OutputRegistry()
    reg.register("dup", lambda cfg, gcfg: _ConcreteBackend())  # type: ignore[arg-type]
    with caplog.at_level(logging.WARNING, logger="casedd.outputs.registry"):
        reg.register("dup", lambda cfg, gcfg: _ConcreteBackend())  # type: ignore[arg-type]

    assert any("Overwriting" in rec.message for rec in caplog.records)


def test_registry_empty_name_raises() -> None:
    """Registering with an empty type name raises ValueError."""
    reg = OutputRegistry()
    with pytest.raises(ValueError, match="non-empty"):
        reg.register("", lambda cfg, gcfg: _ConcreteBackend())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Default registry (framebuffer / websocket built-ins)
# ---------------------------------------------------------------------------


def test_default_registry_has_builtin_types() -> None:
    """get_default_registry() returns a registry with framebuffer + websocket."""
    reg = get_default_registry()
    assert "framebuffer" in reg.registered_types
    assert "websocket" in reg.registered_types


def test_default_registry_singleton() -> None:
    """get_default_registry() always returns the same instance."""
    assert get_default_registry() is get_default_registry()


# ---------------------------------------------------------------------------
# FramebufferOutput as OutputBackend
# ---------------------------------------------------------------------------


def _make_disabled_fb() -> FramebufferOutput:
    """Return a FramebufferOutput, disabled, so no real device is opened."""
    return FramebufferOutput(Path("/dev/null-test"), disabled=True)


def test_framebuffer_is_output_backend() -> None:
    """FramebufferOutput is a subclass of OutputBackend."""
    assert issubclass(FramebufferOutput, OutputBackend)


def test_framebuffer_disabled_not_healthy() -> None:
    """A disabled FramebufferOutput reports is_healthy() == False."""
    fb = _make_disabled_fb()
    assert not fb.is_healthy()  # type: ignore[union-attr]


def test_framebuffer_get_config_returns_device() -> None:
    """FramebufferOutput.get_config() includes the device path."""
    fb = FramebufferOutput(Path("/dev/fb99"), disabled=True)
    config = fb.get_config()
    assert config["device"] == "/dev/fb99"
    assert config["enabled"] is False


@pytest.mark.asyncio
async def test_framebuffer_start_is_noop() -> None:
    """FramebufferOutput.start() completes without error."""
    fb = FramebufferOutput(Path("/dev/null"), disabled=True)
    await fb.start()  # should not raise


@pytest.mark.asyncio
async def test_framebuffer_stop_disables() -> None:
    """FramebufferOutput.stop() disables the output."""
    fb = FramebufferOutput(Path("/dev/null"), disabled=True)
    await fb.stop()
    assert not fb.is_healthy()


@pytest.mark.asyncio
async def test_framebuffer_output_calls_write() -> None:
    """FramebufferOutput.output() delegates to write() via asyncio.to_thread."""
    fb = FramebufferOutput.__new__(FramebufferOutput)
    fb._device = Path("/dev/null")
    fb._enabled = True
    fb._supported_modes = []
    fb._supported_hz = []
    fb._rotation = 0

    written: list[Image.Image] = []

    def _capture(img: Image.Image) -> None:
        written.append(img)

    fb.write = _capture  # type: ignore[method-assign]

    img = Image.new("RGB", (100, 50), (255, 0, 0))
    await fb.output(img)

    assert len(written) == 1
    assert written[0] is img


# ---------------------------------------------------------------------------
# WebSocketOutput as OutputBackend
# ---------------------------------------------------------------------------


def test_websocket_is_output_backend() -> None:
    """WebSocketOutput is a subclass of OutputBackend."""
    assert issubclass(WebSocketOutput, OutputBackend)


def test_websocket_not_healthy_before_start() -> None:
    """WebSocketOutput.is_healthy() returns False before start()."""
    ws = WebSocketOutput("127.0.0.1", 19001)
    assert not ws.is_healthy()


def test_websocket_get_config() -> None:
    """WebSocketOutput.get_config() includes host, port, client_count."""
    ws = WebSocketOutput("127.0.0.1", 19002)
    config = ws.get_config()
    assert config["host"] == "127.0.0.1"
    assert config["port"] == 19002
    assert config["client_count"] == 0


@pytest.mark.asyncio
async def test_websocket_output_delegates_to_broadcast() -> None:
    """WebSocketOutput.output() calls broadcast() with the image."""
    ws = WebSocketOutput("127.0.0.1", 19003)
    broadcast_args: list[Image.Image] = []

    async def _capture_broadcast(img: Image.Image) -> None:
        broadcast_args.append(img)

    ws.broadcast = AsyncMock(side_effect=_capture_broadcast)  # type: ignore[method-assign]

    img = Image.new("RGB", (80, 48), (0, 128, 255))
    await ws.output(img)

    assert len(broadcast_args) == 1
    assert broadcast_args[0] is img


# ---------------------------------------------------------------------------
# OutputBackendConfig Pydantic model
# ---------------------------------------------------------------------------


def test_backend_config_defaults() -> None:
    """OutputBackendConfig fields default correctly."""
    cfg = OutputBackendConfig.model_validate({"type": "framebuffer"})
    assert cfg.type == "framebuffer"
    assert cfg.enabled is True
    assert cfg.width is None
    assert cfg.height is None
    assert cfg.template is None
    assert cfg.refresh_rate is None
    assert cfg.device is None
    assert cfg.rotation is None
    assert cfg.port is None


def test_backend_config_invalid_rotation() -> None:
    """OutputBackendConfig rejects rotation values not in {0, 90, 180, 270}."""
    with pytest.raises(Exception):  # noqa: B017 -- pydantic ValidationError
        OutputBackendConfig.model_validate({"type": "framebuffer", "rotation": 45})


def test_backend_config_valid_rotation() -> None:
    """OutputBackendConfig accepts rotation values 0, 90, 180, 270."""
    for rot in (0, 90, 180, 270):
        cfg = OutputBackendConfig.model_validate({"type": "framebuffer", "rotation": rot})
        assert cfg.rotation == rot


def test_backend_config_extra_fields_ignored() -> None:
    """Extra fields in YAML are silently ignored (extra='ignore')."""
    cfg = OutputBackendConfig.model_validate(
        {"type": "websocket", "port": 8765, "unknown_field": "ignored"}
    )
    assert cfg.type == "websocket"
    assert cfg.port == 8765


def test_backend_config_disabled() -> None:
    """enabled: false is honoured."""
    cfg = OutputBackendConfig.model_validate({"type": "framebuffer", "enabled": False})
    assert cfg.enabled is False


# ---------------------------------------------------------------------------
# Registry factory: framebuffer backend creation
# ---------------------------------------------------------------------------


def test_registry_framebuffer_factory_uses_device() -> None:
    """Framebuffer factory uses cfg.device when provided."""
    reg = get_default_registry()
    global_cfg = MagicMock()
    global_cfg.fb_device = Path("/dev/fb0")
    global_cfg.no_fb = True  # disabled so no real device opens
    global_cfg.fb_rotation = 0

    cfg = OutputBackendConfig.model_validate(
        {"type": "framebuffer", "device": "/dev/fb1", "enabled": True}
    )
    backend = reg.create(cfg, global_cfg)  # type: ignore[arg-type]

    assert isinstance(backend, FramebufferOutput)
    assert backend.get_config()["device"] == "/dev/fb1"


def test_registry_framebuffer_factory_falls_back_to_global_device() -> None:
    """Framebuffer factory falls back to global_cfg.fb_device when cfg.device is None."""
    reg = get_default_registry()
    global_cfg = MagicMock()
    global_cfg.fb_device = Path("/dev/fb2")
    global_cfg.no_fb = True
    global_cfg.fb_rotation = 0

    cfg = OutputBackendConfig.model_validate({"type": "framebuffer"})
    backend = reg.create(cfg, global_cfg)  # type: ignore[arg-type]

    assert isinstance(backend, FramebufferOutput)
    assert backend.get_config()["device"] == "/dev/fb2"


def test_registry_websocket_factory_uses_port() -> None:
    """WebSocket factory uses cfg.port when provided."""
    reg = get_default_registry()
    global_cfg = MagicMock()
    global_cfg.ws_port = 8765

    cfg = OutputBackendConfig.model_validate({"type": "websocket", "port": 9999})
    backend = reg.create(cfg, global_cfg)  # type: ignore[arg-type]

    assert isinstance(backend, WebSocketOutput)
    assert backend.get_config()["port"] == 9999


def test_registry_websocket_factory_falls_back_to_global_port() -> None:
    """WebSocket factory falls back to global_cfg.ws_port when cfg.port is None."""
    reg = get_default_registry()
    global_cfg = MagicMock()
    global_cfg.ws_port = 7777

    cfg = OutputBackendConfig.model_validate({"type": "websocket"})
    backend = reg.create(cfg, global_cfg)  # type: ignore[arg-type]

    assert isinstance(backend, WebSocketOutput)
    assert backend.get_config()["port"] == 7777


# ---------------------------------------------------------------------------
# _ConcreteBackend lifecycle (integration)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concrete_backend_lifecycle() -> None:
    """Full start → output → stop lifecycle works correctly."""
    backend = _ConcreteBackend()

    assert not backend.is_healthy()

    await backend.start()
    assert backend.is_healthy()
    assert backend.started

    img = Image.new("RGB", (64, 48), (10, 20, 30))
    await backend.output(img)
    assert len(backend.received) == 1
    assert backend.received[0] is img

    await backend.stop()
    assert backend.stopped
    assert not backend.is_healthy()


# ---------------------------------------------------------------------------
# scale_for_backend
# ---------------------------------------------------------------------------


def test_scale_for_backend_none_config_returns_same_object() -> None:
    """scale_for_backend(img, None) returns the original image unchanged."""
    img = Image.new("RGB", (800, 480))
    result = scale_for_backend(img, None)
    assert result is img


def test_scale_for_backend_no_dimensions_set_returns_same_object() -> None:
    """scale_for_backend returns original when config has no width/height."""
    img = Image.new("RGB", (800, 480))
    cfg = OutputBackendConfig.model_validate({"type": "websocket"})
    result = scale_for_backend(img, cfg)
    assert result is img


def test_scale_for_backend_same_dimensions_returns_same_object() -> None:
    """scale_for_backend returns original when dimensions already match."""
    img = Image.new("RGB", (1024, 600))
    cfg = OutputBackendConfig.model_validate({"type": "framebuffer", "width": 1024, "height": 600})
    result = scale_for_backend(img, cfg)
    assert result is img


def test_scale_for_backend_both_dimensions_resizes() -> None:
    """scale_for_backend scales to declared width x height."""
    img = Image.new("RGB", (800, 480))
    cfg = OutputBackendConfig.model_validate({"type": "framebuffer", "width": 400, "height": 240})
    result = scale_for_backend(img, cfg)
    assert result.size == (400, 240)
    assert result is not img


def test_scale_for_backend_width_only_scales_proportionally() -> None:
    """scale_for_backend with only width set scales height proportionally."""
    img = Image.new("RGB", (800, 480))
    cfg = OutputBackendConfig.model_validate({"type": "framebuffer", "width": 400})
    result = scale_for_backend(img, cfg)
    assert result.size == (400, 240)  # height = 480 * 400 / 800


def test_scale_for_backend_height_only_scales_proportionally() -> None:
    """scale_for_backend with only height set scales width proportionally."""
    img = Image.new("RGB", (800, 480))
    cfg = OutputBackendConfig.model_validate({"type": "framebuffer", "height": 240})
    result = scale_for_backend(img, cfg)
    assert result.size == (400, 240)  # width = 800 * 240 / 480


# ---------------------------------------------------------------------------
# Multi-backend integration: two backends simultaneously
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_backends_both_receive_every_frame() -> None:
    """Two concurrent backends both receive every dispatched frame."""
    b1 = _ConcreteBackend()
    b2 = _ConcreteBackend()

    await b1.start()
    await b2.start()

    frames = [
        Image.new("RGB", (64, 48), (i * 10, 0, 0))
        for i in range(5)
    ]
    cfg = _make_cfg()
    for frame in frames:
        await b1.output(frame, cfg)
        await b2.output(frame, cfg)

    assert len(b1.received) == 5
    assert len(b2.received) == 5
    # Each backend received exactly the frames that were sent.
    for i, frame in enumerate(frames):
        assert b1.received[i] is frame
        assert b2.received[i] is frame

    await b1.stop()
    await b2.stop()
    assert not b1.is_healthy()
    assert not b2.is_healthy()


@pytest.mark.asyncio
async def test_two_backends_one_stopped_does_not_affect_other() -> None:
    """Stopping one backend mid-stream does not prevent the other from receiving frames."""
    b1 = _ConcreteBackend()
    b2 = _ConcreteBackend()

    await b1.start()
    await b2.start()

    img = Image.new("RGB", (64, 48))
    cfg = _make_cfg()

    await b1.output(img, cfg)
    await b2.output(img, cfg)

    # Stop b1 mid-stream.
    await b1.stop()

    # b2 can still receive frames.
    img2 = Image.new("RGB", (64, 48), (0, 255, 0))
    await b2.output(img2, cfg)

    assert len(b1.received) == 1
    assert len(b2.received) == 2
    assert b2.received[1] is img2


@pytest.mark.asyncio
async def test_scale_for_backend_applied_before_output() -> None:
    """Backend with declared width/height smaller than source receives a scaled image."""
    b = _ConcreteBackend()
    await b.start()

    src = Image.new("RGB", (800, 480), (100, 150, 200))
    cfg = OutputBackendConfig.model_validate({"type": "test", "width": 400, "height": 240})

    # Simulate the dispatch-layer pattern: scale then output.
    scaled = scale_for_backend(src, cfg)
    await b.output(scaled, cfg)

    assert len(b.received) == 1
    assert b.received[0].size == (400, 240)
    assert b.received[0] is not src

    await b.stop()
