"""Tests for RenderEngine two-layer and dirty-check caching."""

from __future__ import annotations

from PIL import Image, ImageDraw

from casedd.data_store import DataStore
from casedd.renderer.engine import RenderEngine
from casedd.renderer.widgets.base import BaseWidget
from casedd.template.grid import Rect
from casedd.template.models import GridConfig, Template, WidgetConfig, WidgetType


class _CountingWidget(BaseWidget):
    """Simple widget renderer that tracks draw invocations."""

    def __init__(self, color: tuple[int, int, int]) -> None:
        self.draw_count = 0
        self._color = color

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        state: dict[str, object],
    ) -> None:
        self.draw_count += 1
        draw = ImageDraw.Draw(img)
        draw.rectangle(
            (rect.x, rect.y, rect.x + rect.w - 1, rect.y + rect.h - 1),
            fill=self._color,
        )


def _build_template() -> Template:
    """Return a simple two-widget template with static and dynamic slots."""
    return Template(
        name="cache-test",
        grid=GridConfig(
            template_areas='"static dynamic"',
            columns="1fr 1fr",
            rows="1fr",
        ),
        widgets={
            "static": WidgetConfig(type=WidgetType.TEXT, content="logo"),
            "dynamic": WidgetConfig(type=WidgetType.VALUE, source="invokeai.queue.pending_count"),
        },
    )


def test_render_engine_static_layer_and_patch_cache(monkeypatch) -> None:
    """Second frame should reuse static layer and dynamic patch cache."""
    static_widget = _CountingWidget((180, 20, 20))
    dynamic_widget = _CountingWidget((20, 180, 20))

    def _fake_registry(widget_type: WidgetType) -> BaseWidget:
        if widget_type == WidgetType.TEXT:
            return static_widget
        if widget_type == WidgetType.VALUE:
            return dynamic_widget
        raise AssertionError(f"Unexpected widget type: {widget_type}")

    monkeypatch.setattr("casedd.renderer.engine.get_widget_renderer", _fake_registry)

    engine = RenderEngine(320, 160)
    template = _build_template()
    store = DataStore()
    store.set("invokeai.queue.pending_count", 5.0)

    first = engine.render(template, store)
    second = engine.render(template, store)

    assert first.size == (320, 160)
    assert second.size == (320, 160)
    assert static_widget.draw_count == 1
    assert dynamic_widget.draw_count == 1

    stats = engine.latest_render_stats()
    assert stats["layout_cache_hit"] is True
    assert stats["static_cache_hit"] is True
    assert stats["dynamic_cached"] == 1


def test_render_engine_redraws_dynamic_widget_on_source_change(monkeypatch) -> None:
    """Dynamic widgets should redraw when source values change."""
    static_widget = _CountingWidget((40, 60, 220))
    dynamic_widget = _CountingWidget((240, 230, 40))

    def _fake_registry(widget_type: WidgetType) -> BaseWidget:
        if widget_type == WidgetType.TEXT:
            return static_widget
        if widget_type == WidgetType.VALUE:
            return dynamic_widget
        raise AssertionError(f"Unexpected widget type: {widget_type}")

    monkeypatch.setattr("casedd.renderer.engine.get_widget_renderer", _fake_registry)

    engine = RenderEngine(320, 160)
    template = _build_template()
    store = DataStore()
    store.set("invokeai.queue.pending_count", 1.0)
    engine.render(template, store)

    store.set("invokeai.queue.pending_count", 9.0)
    engine.render(template, store)

    assert static_widget.draw_count == 1
    assert dynamic_widget.draw_count == 2
    stats = engine.latest_render_stats()
    assert stats["dynamic_drawn"] == 1
    assert stats["dynamic_cached"] == 0
