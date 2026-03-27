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

from PIL import Image

from casedd.data_store import DataStore
from casedd.renderer.color import parse_color
from casedd.renderer.widgets.base import draw_widget_border
from casedd.renderer.widgets.registry import get_widget_renderer
from casedd.template.grid import resolve_grid
from casedd.template.models import Template

_log = logging.getLogger(__name__)

# Re-export so external code (panel.py historically) can import from here
__all__ = ["RenderEngine", "get_widget_renderer"]


class RenderEngine:
    """Stateful frame renderer.

    Holds per-widget state dicts across calls to :meth:`render` so that
    widgets like :class:`~casedd.renderer.widgets.histogram.HistogramWidget`
    maintain their rolling buffers.

    Args:
        width: Canvas width in pixels.
        height: Canvas height in pixels.
    """

    def __init__(self, width: int, height: int) -> None:
        """Initialise the render engine.

        Args:
            width: Canvas width in pixels (from config, not template — template
                   may override per-render).
            height: Canvas height in pixels.
        """
        self._default_w = width
        self._default_h = height
        # widget_name → mutable state dict (history buffers, cached images, etc.)
        self._widget_states: dict[str, dict[str, object]] = {}

    def render(self, template: Template, data: DataStore) -> Image.Image:
        """Render one frame.

        This method runs synchronously (CPU-bound). The daemon wraps it in
        ``asyncio.to_thread`` to avoid blocking the event loop.

        Args:
            template: The parsed and validated template to render.
            data: The live data store snapshot to use for this frame.

        Returns:
            A PIL ``Image`` in RGB mode at the template's canvas dimensions.
        """
        w = template.width or self._default_w
        h = template.height or self._default_h

        # Create a fresh canvas with the template background color
        bg_rgb = parse_color(template.background, fallback=(0, 0, 0))
        img = Image.new("RGB", (w, h), bg_rgb)

        # Resolve the CSS grid to pixel rects for all top-level widgets
        rects = resolve_grid(
            template_areas=template.grid.template_areas,
            columns=template.grid.columns,
            rows=template.grid.rows,
            canvas_w=w,
            canvas_h=h,
        )

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

        _log.debug("Rendered frame: %dx%d, %d widgets", w, h, len(template.widgets))
        return img
