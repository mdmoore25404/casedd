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

import logging
import threading

from PIL import Image

from casedd.data_store import DataStore
from casedd.renderer.color import parse_color
from casedd.renderer.widgets.base import _normalize_padding, draw_widget_border
from casedd.renderer.widgets.registry import get_widget_renderer
from casedd.template.grid import Rect, resolve_grid
from casedd.template.models import Template

_log = logging.getLogger(__name__)

# Re-export so external code (panel.py historically) can import from here
__all__ = ["RenderEngine", "get_widget_renderer"]


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
        with self._state_lock:
            # The panel runtime owns the real output size. Template dimensions
            # are design-time metadata and should only act as a fallback when
            # the engine was not constructed with an explicit panel size.
            w = self._default_w if self._default_w > 0 else (template.width or 800)
            h = self._default_h if self._default_h > 0 else (template.height or 480)

            # Create a fresh canvas with the template background color
            bg_rgb = parse_color(template.background, fallback=(0, 0, 0))
            img = Image.new("RGB", (w, h), bg_rgb)

            # Apply display padding to create an inset content area. The
            # surrounding border keeps the template background colour.
            pad_t, pad_r, pad_b, pad_l = self._display_padding
            inner_x = pad_l
            inner_y = pad_t
            inner_w = max(1, w - pad_l - pad_r)
            inner_h = max(1, h - pad_t - pad_b)
            viewport = Rect(inner_x, inner_y, inner_w, inner_h)

            aspect_ratio = _parse_aspect_ratio(template)
            if aspect_ratio is not None and template.layout_mode.value == "fit":
                # Letterbox within the already-padded inner area.
                fit_rect = _fit_layout_rect(inner_w, inner_h, aspect_ratio)
                viewport = Rect(
                    inner_x + fit_rect.x,
                    inner_y + fit_rect.y,
                    fit_rect.w,
                    fit_rect.h,
                )

            # Resolve the CSS grid to pixel rects for all top-level widgets
            rects = resolve_grid(
                template_areas=template.grid.template_areas,
                columns=template.grid.columns,
                rows=template.grid.rows,
                canvas_w=viewport.w,
                canvas_h=viewport.h,
            )

            rects = {
                name: Rect(
                    viewport.x + rect.x,
                    viewport.y + rect.y,
                    rect.w,
                    rect.h,
                )
                for name, rect in rects.items()
            }

            # Render each widget into its allocated rect
            for name, cfg in template.widgets.items():
                rect = rects.get(name)
                if rect is None:
                    _log.warning("Widget '%s' has no grid rect — skipping.", name)
                    continue

                # Retrieve or create per-widget mutable state
                if name not in self._widget_states:
                    self._widget_states[name] = {}
                state = self._widget_states[name]

                try:
                    renderer = get_widget_renderer(cfg.type)
                    renderer.draw(img, rect, cfg, data, state)
                    draw_widget_border(img, rect, cfg)
                except Exception:
                    _log.exception("Widget '%s' (%s) raised during render:", name, cfg.type)

        self._frame_count += 1
        # Avoid per-frame log spam and overhead in long-running sessions.
        if self._debug_frame_logs and self._frame_count % 120 == 0:
            _log.debug("Rendered frame: %dx%d, %d widgets", w, h, len(template.widgets))
        return img

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
