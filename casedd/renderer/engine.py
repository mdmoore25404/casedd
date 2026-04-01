"""Top-level PIL image renderer for CASEDD.

Takes a parsed :class:`~casedd.template.models.Template` and a
:class:`~casedd.data_store.DataStore` and produces a ``PIL.Image.Image``
ready for output to framebuffer and WebSocket.

The engine maintains per-widget state dicts (e.g. rolling history buffers for
histogram and sparkline widgets) across frames without exposing them to callers.

Public API:
    - :class:`RenderEngine` — create one instance, call :meth:`render` each frame
    - :func:`get_widget_renderer` — re-exported for panel.py's use
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging
import threading
import time

from PIL import Image

from casedd.data_store import DataStore
from casedd.renderer.color import parse_color
from casedd.renderer.widgets.base import _normalize_padding, draw_widget_border
from casedd.renderer.widgets.registry import get_widget_renderer
from casedd.template.grid import Rect, resolve_grid
from casedd.template.models import Template, WidgetConfig, WidgetType

_log = logging.getLogger(__name__)

# Re-export so external code (panel.py historically) can import from here
__all__ = ["RenderEngine", "get_widget_renderer"]

_INTRINSIC_DYNAMIC_WIDGET_TYPES: set[WidgetType] = {
    WidgetType.CLOCK,
    WidgetType.HISTOGRAM,
    WidgetType.SPARKLINE,
    WidgetType.SLIDESHOW,
    WidgetType.HTOP,
    WidgetType.NET_PORTS,
    WidgetType.SYSINFO,
    WidgetType.APOD,
    WidgetType.WEATHER_CONDITIONS,
    WidgetType.WEATHER_FORECAST,
    WidgetType.WEATHER_ALERTS,
    WidgetType.WEATHER_RADAR,
    WidgetType.PLEX_NOW_PLAYING,
    WidgetType.PLEX_RECENTLY_ADDED,
    WidgetType.OLLAMA,
}

_PATCH_CACHEABLE_WIDGET_TYPES: set[WidgetType] = {
    WidgetType.BOOLEAN,
    WidgetType.VALUE,
    WidgetType.TEXT,
    WidgetType.BAR,
    WidgetType.GAUGE,
    WidgetType.IMAGE,
}


def _is_widget_static(cfg: WidgetConfig) -> bool:
    """Return True when a widget can be pre-rendered into a static layer."""
    if cfg.source:
        return False

    if cfg.type in _INTRINSIC_DYNAMIC_WIDGET_TYPES:
        return False

    if cfg.type == WidgetType.IMAGE and cfg.tiers:
        return False

    if cfg.type != WidgetType.PANEL:
        return True

    child_widgets = cfg.children if cfg.children else list(cfg.children_named.values())
    if not child_widgets:
        return True
    return all(_is_widget_static(child) for child in child_widgets)


def _build_widget_token(
    cfg: WidgetConfig,
    snapshot: Mapping[str, object],
) -> tuple[object, ...]:
    """Build a compact token representing values that affect widget output."""
    token: list[object] = [id(cfg)]
    if cfg.source:
        token.append(snapshot.get(cfg.source))
    if cfg.type == WidgetType.IMAGE:
        for tier in cfg.tiers:
            for condition in tier.when:
                token.append(condition.source)
                token.append(snapshot.get(condition.source))
    return tuple(token)


def _parse_aspect_ratio(template: Template) -> float | None:
    """Resolve the template's logical aspect ratio, if any."""
    if template.aspect_ratio:
        raw = template.aspect_ratio.strip()
        if ":" in raw:
            left_raw, right_raw = raw.split(":", maxsplit=1)
            left = float(left_raw)
            right = float(right_raw)
            return left / right if right > 0 else None
        ratio = float(raw)
        return ratio if ratio > 0 else None

    if template.width and template.height and template.height > 0:
        return template.width / template.height
    return None


def _fit_layout_rect(canvas_w: int, canvas_h: int, aspect_ratio: float) -> Rect:
    """Return a centered letterboxed rect that preserves aspect ratio."""
    canvas_ratio = canvas_w / canvas_h if canvas_h > 0 else aspect_ratio
    if canvas_ratio > aspect_ratio:
        viewport_h = canvas_h
        viewport_w = max(1, round(viewport_h * aspect_ratio))
        viewport_x = (canvas_w - viewport_w) // 2
        viewport_y = 0
    else:
        viewport_w = canvas_w
        viewport_h = max(1, round(viewport_w / aspect_ratio))
        viewport_x = 0
        viewport_y = (canvas_h - viewport_h) // 2
    return Rect(viewport_x, viewport_y, viewport_w, viewport_h)


@dataclass(frozen=True)
class _DynamicRenderContext:
    """Inputs for dynamic widget rendering."""

    img: Image.Image
    static_layer: Image.Image
    template: Template
    rects: dict[str, Rect]
    dynamic_widgets: tuple[str, ...]
    data: DataStore
    snapshot: Mapping[str, object]


class RenderEngine:
    """Stateful frame renderer.

    Holds per-widget state dicts across calls to :meth:`render` so that
    widgets like :class:`~casedd.renderer.widgets.histogram.HistogramWidget`
    maintain their rolling buffers.

    Args:
        width: Canvas width in pixels.
        height: Canvas height in pixels.
    """

    def __init__(
        self,
        width: int,
        height: int,
        *,
        debug_frame_logs: bool = False,
        display_padding: int | list[int] = 0,
    ) -> None:
        """Initialise the render engine.

        Args:
            width: Canvas width in pixels. When non-zero, this is the effective
                panel/output size and takes precedence over any template-embedded
                dimensions.
            height: Canvas height in pixels.
            debug_frame_logs: Enable per-frame renderer debug logging.
            display_padding: Padding in pixels applied between the physical
                edge and the rendered content area.  Accepts the same
                int / [v, h] / [t, r, b, l] shorthand as widget padding.
        """
        self._default_w = width
        self._default_h = height
        self._debug_frame_logs = debug_frame_logs
        self._display_padding = _normalize_padding(
            display_padding if isinstance(display_padding, int) else list(display_padding)
        )
        # widget_name → mutable state dict (history buffers, cached images, etc.)
        self._widget_states: dict[str, dict[str, object]] = {}
        self._state_lock = threading.Lock()
        self._frame_count = 0
        self._plan_cache: dict[int, tuple[tuple[str, ...], tuple[str, ...]]] = {}
        self._rect_cache: dict[tuple[int, int, int], dict[str, Rect]] = {}
        self._static_layer_cache: dict[tuple[int, int, int], Image.Image] = {}
        self._last_render_stats: dict[str, object] = {
            "layout_cache_hit": False,
            "static_cache_hit": False,
            "dynamic_drawn": 0,
            "dynamic_cached": 0,
            "render_ms": 0.0,
            "static_ms": 0.0,
            "dynamic_ms": 0.0,
            "widget_count": 0,
        }

    def _resolve_viewport(self, template: Template, w: int, h: int) -> Rect:
        """Resolve output viewport after display padding and optional fit mode."""
        pad_t, pad_r, pad_b, pad_l = self._display_padding
        inner_x = pad_l
        inner_y = pad_t
        inner_w = max(1, w - pad_l - pad_r)
        inner_h = max(1, h - pad_t - pad_b)
        viewport = Rect(inner_x, inner_y, inner_w, inner_h)

        aspect_ratio = _parse_aspect_ratio(template)
        if aspect_ratio is not None and template.layout_mode.value == "fit":
            fit_rect = _fit_layout_rect(inner_w, inner_h, aspect_ratio)
            return Rect(
                inner_x + fit_rect.x,
                inner_y + fit_rect.y,
                fit_rect.w,
                fit_rect.h,
            )
        return viewport

    def _resolve_rects(
        self,
        template: Template,
        w: int,
        h: int,
    ) -> tuple[dict[str, Rect], bool]:
        """Resolve and cache top-level widget rectangles for one template size."""
        template_key = id(template)
        cache_key = (template_key, w, h)
        cached_rects = self._rect_cache.get(cache_key)
        if cached_rects is not None:
            return cached_rects, True

        viewport = self._resolve_viewport(template, w, h)
        local_rects = resolve_grid(
            template_areas=template.grid.template_areas,
            columns=template.grid.columns,
            rows=template.grid.rows,
            canvas_w=viewport.w,
            canvas_h=viewport.h,
        )
        absolute_rects = {
            name: Rect(
                viewport.x + rect.x,
                viewport.y + rect.y,
                rect.w,
                rect.h,
            )
            for name, rect in local_rects.items()
        }
        self._rect_cache[cache_key] = absolute_rects
        return absolute_rects, False

    def _resolve_plan(self, template: Template) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Return cached split of static and dynamic top-level widget names."""
        template_key = id(template)
        plan = self._plan_cache.get(template_key)
        if plan is not None:
            return plan

        static_widgets: list[str] = []
        dynamic_widgets: list[str] = []
        for name, cfg in template.widgets.items():
            if _is_widget_static(cfg):
                static_widgets.append(name)
            else:
                dynamic_widgets.append(name)

        resolved = (tuple(static_widgets), tuple(dynamic_widgets))
        self._plan_cache[template_key] = resolved
        return resolved

    def _build_static_layer(
        self,
        template: Template,
        rects: dict[str, Rect],
        static_widgets: tuple[str, ...],
        size: tuple[int, int],
    ) -> Image.Image:
        """Create and cache the static frame background and static widgets."""
        w, h = size
        static_key = (id(template), w, h)
        bg_rgb = parse_color(template.background, fallback=(0, 0, 0))
        image = Image.new("RGB", (w, h), bg_rgb)

        for name in static_widgets:
            cfg = template.widgets.get(name)
            rect = rects.get(name)
            if cfg is None or rect is None:
                continue

            state = self._widget_states.setdefault(name, {})
            try:
                renderer = get_widget_renderer(cfg.type)
                renderer.draw(image, rect, cfg, DataStore(), state)
                draw_widget_border(image, rect, cfg)
            except Exception:
                _log.exception("Widget '%s' (%s) raised during static render:", name, cfg.type)

        self._static_layer_cache[static_key] = image
        return image

    def _get_static_layer(
        self,
        template: Template,
        rects: dict[str, Rect],
        static_widgets: tuple[str, ...],
        size: tuple[int, int],
    ) -> tuple[Image.Image, bool, float]:
        """Return cached static layer and build timing metadata."""
        started = time.perf_counter()
        static_key = (id(template), size[0], size[1])
        static_layer = self._static_layer_cache.get(static_key)
        static_cache_hit = static_layer is not None
        if static_layer is None:
            static_layer = self._build_static_layer(template, rects, static_widgets, size)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return static_layer, static_cache_hit, elapsed_ms

    def _render_dynamic_widgets(
        self,
        context: _DynamicRenderContext,
    ) -> tuple[int, int, float]:
        """Render dynamic widgets and return draw/cache counters with elapsed ms."""
        started = time.perf_counter()
        dynamic_drawn = 0
        dynamic_cached = 0
        for name in context.dynamic_widgets:
            cfg = context.template.widgets.get(name)
            rect = context.rects.get(name)
            if cfg is None or rect is None:
                _log.warning("Widget '%s' has no grid rect — skipping.", name)
                continue

            state = self._widget_states.setdefault(name, {})
            try:
                renderer = get_widget_renderer(cfg.type)
                if self._can_patch_cache_widget(cfg):
                    token = _build_widget_token(cfg, context.snapshot)
                    cached_token = state.get("frame_token")
                    cached_patch = state.get("frame_patch")
                    cached_size = state.get("frame_patch_size")
                    if (
                        cached_token == token
                        and isinstance(cached_patch, Image.Image)
                        and cached_size == (rect.w, rect.h)
                    ):
                        context.img.paste(cached_patch, (rect.x, rect.y))
                        dynamic_cached += 1
                        continue

                    patch = context.static_layer.crop(
                        (rect.x, rect.y, rect.x + rect.w, rect.y + rect.h)
                    )
                    local_rect = Rect(0, 0, rect.w, rect.h)
                    renderer.draw(patch, local_rect, cfg, context.data, state)
                    draw_widget_border(patch, local_rect, cfg)
                    state["frame_token"] = token
                    state["frame_patch"] = patch.copy()
                    state["frame_patch_size"] = (rect.w, rect.h)
                    context.img.paste(patch, (rect.x, rect.y))
                    dynamic_drawn += 1
                    continue

                renderer.draw(context.img, rect, cfg, context.data, state)
                draw_widget_border(context.img, rect, cfg)
                dynamic_drawn += 1
            except Exception:
                _log.exception("Widget '%s' (%s) raised during render:", name, cfg.type)

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return dynamic_drawn, dynamic_cached, elapsed_ms

    @staticmethod
    def _can_patch_cache_widget(cfg: WidgetConfig) -> bool:
        """Return True when widget output can be safely patch-cached."""
        return cfg.type in _PATCH_CACHEABLE_WIDGET_TYPES

    def render(self, template: Template, data: DataStore) -> Image.Image:
        """Render one frame.

        This method runs synchronously (CPU-bound). The daemon wraps it in
        ``asyncio.to_thread`` to avoid blocking the event loop.

        Args:
            template: The parsed and validated template to render.
            data: The live data store snapshot to use for this frame.

        Returns:
            A PIL ``Image`` in RGB mode at the active panel/output dimensions.
        """
        render_started = time.perf_counter()
        with self._state_lock:
            w = self._default_w if self._default_w > 0 else (template.width or 800)
            h = self._default_h if self._default_h > 0 else (template.height or 480)
            rects, layout_cache_hit = self._resolve_rects(template, w, h)
            static_widgets, dynamic_widgets = self._resolve_plan(template)
            size = (w, h)

            static_layer, static_cache_hit, static_elapsed_ms = self._get_static_layer(
                template,
                rects,
                static_widgets,
                size,
            )

            img = static_layer.copy()
            snapshot = data.snapshot()

            dynamic_context = _DynamicRenderContext(
                img=img,
                static_layer=static_layer,
                template=template,
                rects=rects,
                dynamic_widgets=dynamic_widgets,
                data=data,
                snapshot=snapshot,
            )
            dynamic_drawn, dynamic_cached, dynamic_elapsed_ms = self._render_dynamic_widgets(
                dynamic_context,
            )
            total_elapsed_ms = (time.perf_counter() - render_started) * 1000.0
            self._last_render_stats = {
                "layout_cache_hit": layout_cache_hit,
                "static_cache_hit": static_cache_hit,
                "dynamic_drawn": dynamic_drawn,
                "dynamic_cached": dynamic_cached,
                "render_ms": total_elapsed_ms,
                "static_ms": static_elapsed_ms,
                "dynamic_ms": dynamic_elapsed_ms,
                "widget_count": len(template.widgets),
            }

        self._frame_count += 1
        # Avoid per-frame log spam and overhead in long-running sessions.
        if self._debug_frame_logs and self._frame_count % 120 == 0:
            _log.debug("Rendered frame: %dx%d, %d widgets", w, h, len(template.widgets))
        return img

    def latest_render_stats(self) -> dict[str, object]:
        """Return the latest renderer performance and cache statistics."""
        with self._state_lock:
            return dict(self._last_render_stats)

    def debug_state_snapshot(self) -> dict[str, object]:
        """Return JSON-serializable history state for sparkline/histogram widgets.

        Returns:
            Dict keyed by widget name with ``buf`` and/or ``multi_buf`` values.
        """
        with self._state_lock:
            snapshot: dict[str, object] = {}
            for widget_name, state in self._widget_states.items():
                widget_payload: dict[str, object] = {}

                buf_obj = state.get("buf")
                if buf_obj is not None and hasattr(buf_obj, "__iter__"):
                    widget_payload["buf"] = [
                        float(sample[1])
                        for sample in list(buf_obj)
                        if isinstance(sample, tuple) and len(sample) == 2
                    ]

                multi_obj = state.get("multi_buf")
                if isinstance(multi_obj, dict):
                    multi_payload: dict[str, list[float]] = {}
                    for source, source_buf in multi_obj.items():
                        if hasattr(source_buf, "__iter__"):
                            multi_payload[str(source)] = [
                                float(sample[1])
                                for sample in list(source_buf)
                                if isinstance(sample, tuple) and len(sample) == 2
                            ]
                    widget_payload["multi_buf"] = multi_payload

                if widget_payload:
                    snapshot[widget_name] = widget_payload

            return snapshot
