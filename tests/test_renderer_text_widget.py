"""Tests for :mod:`casedd.renderer.widgets.text`."""

from __future__ import annotations

from PIL import Image, ImageFont

from casedd.data_store import DataStore
from casedd.renderer.fonts import get_font
from casedd.renderer.widgets.text import TextWidget, _wrap_text
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig, WidgetType


def test_wrap_text_splits_oversized_tokens() -> None:
    """Long single tokens should be split into multiple wrapped lines."""
    font = get_font(18)
    lines = _wrap_text("averyverylongsinglewordwithoutspaces", font, max_width=80)

    assert len(lines) > 1
    for line in lines:
        bbox = font.getbbox(line)
        width = bbox[2] - bbox[0]
        assert width <= 80


def test_text_widget_reuses_layout_cache_on_repeat_draw() -> None:
    """Repeated draws with unchanged text should reuse cached fit/wrap layout."""
    img = Image.new("RGB", (320, 120), (0, 0, 0))
    store = DataStore()
    store.set("nzbget.current_1.name", "Example.Download.Release.Group")

    class _CountingTextWidget(TextWidget):
        def __init__(self) -> None:
            self.calls = 0

        def _fit_wrapped_font(
            self,
            text: str,
            max_w: int,
            max_h: int,
            font_size: int | str,
        ) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, list[str]]:
            self.calls += 1
            return super()._fit_wrapped_font(text, max_w, max_h, font_size)

    widget = _CountingTextWidget()
    cfg = WidgetConfig(
        type=WidgetType.TEXT,
        source="nzbget.current_1.name",
        font_size="auto",
    )
    state: dict[str, object] = {}

    widget.draw(img, Rect(x=0, y=0, w=320, h=120), cfg, store, state)
    widget.draw(img, Rect(x=0, y=0, w=320, h=120), cfg, store, state)

    assert widget.calls == 1
    assert "text_layout_key" in state
