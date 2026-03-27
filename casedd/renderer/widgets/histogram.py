"""Histogram widget renderer.

Maintains a rolling sample buffer and draws a bar chart of recent history.

Example .casedd config:

.. code-block:: yaml

    ram_hist:
      type: histogram
      source: memory.percent
      samples: 60
      label: "RAM History"
      color: "#4d96ff"
      min: 0
      max: 100
"""

from __future__ import annotations

from collections import deque

from PIL import Image, ImageDraw

from casedd.data_store import DataStore
from casedd.renderer.color import interpolate_color_stops, parse_color
from casedd.renderer.widgets.base import (
    BaseWidget,
    draw_label,
    fill_background,
    resolve_value,
)
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig

_BAR_BG = (35, 35, 35)
_GAP = 1  # pixel gap between bars


class HistogramWidget(BaseWidget):
    """Renders a rolling bar chart of sampled values.

    Maintains its own ``deque`` in ``state`` keyed by widget name.
    """

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        state: dict[str, object],
    ) -> None:
        """Paint the histogram onto ``img``.

        Updates the internal sample buffer from the current data store value.

        Args:
            img: Canvas image.
            rect: Widget bounding box.
            cfg: Widget configuration.
            data: Live data store.
            state: Mutable state dict — stores the ``deque`` for this widget.
        """
        fill_background(img, rect, cfg.background)
        draw = ImageDraw.Draw(img)

        label_h = 0
        if cfg.label:
            label_h = draw_label(draw, rect, cfg.label, color=(150, 150, 150))

        # Initialise or retrieve the rolling sample buffer
        buf_key = "buf"
        if buf_key not in state:
            state[buf_key] = deque[float](maxlen=cfg.samples)
        buf: deque[float] = state[buf_key]  # type: ignore[assignment]

        # Sample current value
        raw = resolve_value(cfg, data)
        try:
            value = float(raw) if raw is not None else cfg.min
        except (ValueError, TypeError):
            value = cfg.min
        buf.append(max(cfg.min, min(cfg.max, value)))

        # Draw background track
        area_x = rect.x + 2
        area_y = rect.y + label_h + 2
        area_w = rect.w - 4
        area_h = rect.h - label_h - 4
        draw.rectangle([area_x, area_y, area_x + area_w, area_y + area_h], fill=_BAR_BG)

        if not buf:
            return

        bar_total_w = area_w / cfg.samples  # float -- bars may be < 1px wide
        span = cfg.max - cfg.min

        for i, sample in enumerate(buf):
            ratio = (sample - cfg.min) / span if span > 0 else 0.0
            bar_h = max(1, int(area_h * ratio))
            bx = area_x + int(i * bar_total_w)
            bw = max(1, int(bar_total_w) - _GAP)
            by = area_y + area_h - bar_h

            if cfg.color_stops:
                fill_rgb = interpolate_color_stops(sample, cfg.color_stops, cfg.min, cfg.max)
            else:
                fill_rgb = parse_color(cfg.color, fallback=(70, 130, 200))

            draw.rectangle([bx, by, bx + bw, area_y + area_h], fill=fill_rgb)
