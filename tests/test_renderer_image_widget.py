"""Tests for :mod:`casedd.renderer.widgets.image`."""

from __future__ import annotations

from io import BytesIO
from urllib.error import URLError

from PIL import Image

from casedd.data_store import DataStore
from casedd.renderer.widgets import image as image_mod
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

    def _ok(req, timeout: float, context=None):
        request_count["count"] += 1
        return _FakeResponse(payload.getvalue())

    image_widget_module._image_cache.clear()
    image_widget_module._image_mtime_cache.clear()
    image_widget_module._image_retry_after.clear()
    image_widget_module._remote_image_cache.clear()
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


def test_image_widget_remote_failures_use_retry_backoff(monkeypatch) -> None:
    """Widget should avoid repeated remote retries every frame for bad URLs."""
    img = Image.new("RGB", (200, 120), (0, 0, 0))
    request_count = {"count": 0}

    def _fail(req, timeout: float, context=None):
        request_count["count"] += 1
        raise URLError("boom")

    image_widget_module._image_cache.clear()
    image_widget_module._image_mtime_cache.clear()
    image_widget_module._image_retry_after.clear()
    image_widget_module._remote_image_cache.clear()
    monkeypatch.setattr("casedd.renderer.widgets.image.urlopen", _fail)

    store = DataStore()
    store.set("invokeai.preview.url", "http://bandit:9090/bad/path.png")

    widget = ImageWidget()
    cfg = WidgetConfig(type=WidgetType.IMAGE, source="invokeai.preview.url")
    state: dict[str, object] = {}
    widget.draw(img, Rect(x=0, y=0, w=200, h=120), cfg, store, state)
    widget.draw(img, Rect(x=0, y=0, w=200, h=120), cfg, store, state)

    assert request_count["count"] == 1


def test_remote_image_cache_is_bounded(monkeypatch) -> None:
    """Remote image cache must never exceed _REMOTE_IMAGE_CACHE_MAXSIZE entries.

    Regression guard for the Synology camera snapshot leak: each getter poll
    generates a unique URL (_ts= timestamp).  The LRU cache must evict the
    oldest entry rather than accumulating one PIL Image per unique URL.
    """
    image_mod._remote_image_cache.clear()
    image_mod._image_retry_after.clear()

    call_count = {"n": 0}

    def _fake_urlopen(req: object, timeout: float, context: object = None) -> _FakeResponse:
        call_count["n"] += 1
        pixel = Image.new("RGBA", (1, 1), (10, 20, 30, 255))
        buf = BytesIO()
        pixel.save(buf, format="PNG")
        return _FakeResponse(buf.getvalue())

    monkeypatch.setattr("casedd.renderer.widgets.image.urlopen", _fake_urlopen)

    maxsize = image_mod._REMOTE_IMAGE_CACHE_MAXSIZE
    # Simulate getter polling with a unique URL each time (like _ts= timestamps).
    for i in range(maxsize + 10):
        url = f"http://camera.local/snapshot?_ts={i}"
        image_mod._load_image(url)

    assert len(image_mod._remote_image_cache) <= maxsize
    # All fetches should have been made (each URL is unique, no cache hits).
    assert call_count["n"] == maxsize + 10
    image_mod._remote_image_cache.clear()
    image_mod._image_retry_after.clear()


def test_image_widget_state_cache_prevents_reload_on_each_frame(monkeypatch) -> None:
    """Per-widget state cache must prevent _load_image being called every frame.

    When the source URL has not changed between frames, _load_image (and any
    network/disk I/O it may trigger) must NOT be called — the scaled image in
    widget state is sufficient.
    """
    image_mod._remote_image_cache.clear()
    image_mod._image_retry_after.clear()

    load_calls: list[str] = []

    real_load = image_mod._load_image

    def _counting_load(source_ref: str) -> Image.Image | None:
        load_calls.append(source_ref)
        return real_load(source_ref)

    pixel = Image.new("RGBA", (1, 1), (50, 100, 150, 255))
    payload = BytesIO()
    pixel.save(payload, format="PNG")

    def _fake_urlopen(req: object, timeout: float, context: object = None) -> _FakeResponse:
        return _FakeResponse(payload.getvalue())

    monkeypatch.setattr("casedd.renderer.widgets.image.urlopen", _fake_urlopen)
    monkeypatch.setattr("casedd.renderer.widgets.image._load_image", _counting_load)

    store = DataStore()
    url = "http://camera.local/snap?_ts=12345"
    store.set("cam.url", url)

    widget = ImageWidget()
    cfg = WidgetConfig(type=WidgetType.IMAGE, source="cam.url")
    canvas = Image.new("RGB", (200, 120))
    state: dict[str, object] = {}

    # First draw: must load the image once.
    widget.draw(canvas, Rect(x=0, y=0, w=200, h=120), cfg, store, state)
    assert load_calls.count(url) == 1

    # Subsequent draws with the same URL: state cache must be hit, no reload.
    widget.draw(canvas, Rect(x=0, y=0, w=200, h=120), cfg, store, state)
    widget.draw(canvas, Rect(x=0, y=0, w=200, h=120), cfg, store, state)
    assert load_calls.count(url) == 1  # still only 1 load across 3 draws

    image_mod._remote_image_cache.clear()
    image_mod._image_retry_after.clear()

