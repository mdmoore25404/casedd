"""Tests for :mod:`casedd.renderer.widgets.image`."""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from casedd.data_store import DataStore
from casedd.renderer.widgets import image as image_widget_module
from casedd.renderer.widgets.image import ImageWidget
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig, WidgetType


class _FakeResponse:
    """Minimal response object for remote image fetch tests."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_image_widget_uses_store_backed_local_path(tmp_path) -> None:
    """Image widget should render a local path provided by cfg.source."""
    img = Image.new("RGB", (200, 120), (0, 0, 0))
    preview_path = tmp_path / "preview.png"
    Image.new("RGBA", (40, 40), (200, 20, 20, 255)).save(preview_path)

    store = DataStore()
    store.set("invokeai.preview.path", str(preview_path))

    widget = ImageWidget()
    cfg = WidgetConfig(type=WidgetType.IMAGE, source="invokeai.preview.path")
    widget.draw(img, Rect(x=0, y=0, w=200, h=120), cfg, store, {})

    assert img.getbbox() is not None


def test_image_widget_uses_store_backed_remote_url(monkeypatch) -> None:
    """Image widget should fetch and cache a remote URL provided by cfg.source."""
    img = Image.new("RGB", (200, 120), (0, 0, 0))
    remote_source = Image.new("RGBA", (32, 32), (20, 120, 220, 255))
    payload = BytesIO()
    remote_source.save(payload, format="PNG")
    request_count = {"count": 0}

    def _ok(req, timeout: float):
        request_count["count"] += 1
        return _FakeResponse(payload.getvalue())

    image_widget_module._image_cache.clear()
    monkeypatch.setattr("casedd.renderer.widgets.image.urlopen", _ok)

    store = DataStore()
    store.set("invokeai.preview.url", "http://bandit:9090/api/v1/images/i/job.png/thumbnail")

    widget = ImageWidget()
    cfg = WidgetConfig(type=WidgetType.IMAGE, source="invokeai.preview.url")
    state: dict[str, object] = {}
    widget.draw(img, Rect(x=0, y=0, w=200, h=120), cfg, store, state)
    widget.draw(img, Rect(x=0, y=0, w=200, h=120), cfg, store, state)

    assert img.getbbox() is not None
    assert request_count["count"] == 1
