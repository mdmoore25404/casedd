"""Bar widget renderer.

Horizontal progress bar with optional label, value text, and color_stops.

Example .casedd config:

.. code-block:: yaml

    ram:
      type: bar
      source: memory.percent
      label: "RAM"
      min: 0
      max: 100
      color_stops:
        - [0,  "#6bcb77"]
        - [80, "#ffd93d"]
        - [95, "#ff6b6b"]
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from casedd.data_store import DataStore
from casedd.renderer.color import interpolate_color_stops, parse_color
from casedd.renderer.fonts import get_font
from casedd.renderer.widgets.base import (
    BaseWidget,
    content_rect,
    draw_label,
    fill_background,
    resolve_value,
)
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig

_BAR_BG = (40, 40, 40)         # unfilled track color
_BAR_RADIUS = 4                 # rounded rectangle corner radius


class BarWidget(BaseWidget):
    """Renders a horizontal progress bar."""

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        _state: dict[str, object],
    ) -> None:
        """Paint the bar widget onto ``img``.

        Args:
            img: Canvas image.
            rect: Widget bounding box.
            cfg: Widget configuration.
            data: Live data store.
            _state: Unused for this widget type.
        """
        fill_background(img, rect, cfg.background)
        inner = content_rect(rect, cfg.padding)
        draw = ImageDraw.Draw(img)

        label_h = 0
        if cfg.label:
            label_h = draw_label(draw, inner, cfg.label, color=(150, 150, 150))

        # Obtain numeric value; clamp to [min, max]
        raw = resolve_value(cfg, data)
        try:
            value = float(raw) if raw is not None else cfg.min
        except (ValueError, TypeError):
            value = cfg.min
        value = max(cfg.min, min(cfg.max, value))

        # Choose fill color
        if cfg.color_stops:
            fill_rgb = interpolate_color_stops(value, cfg.color_stops, cfg.min, cfg.max)
        else:
            fill_rgb = parse_color(cfg.color, fallback=(70, 130, 200))

        # Bar geometry — leave 4px margins on each side
        margin = 4
        bar_x = inner.x + margin
        bar_y = inner.y + label_h + margin
        bar_w = inner.w - margin * 2
        bar_h = inner.h - label_h - margin * 2

        # Ensure minimum usable sizes
        bar_w = max(bar_w, 4)
        bar_h = max(bar_h, 6)

        # Draw the unfilled track
        draw.rounded_rectangle(
            [bar_x, bar_y, bar_x + bar_w, bar_y + bar_h],
            radius=_BAR_RADIUS,
            fill=_BAR_BG,
        )

        # Draw the filled portion
        span = cfg.max - cfg.min
        ratio = (value - cfg.min) / span if span > 0 else 0.0
        filled_w = max(0, int(bar_w * ratio))
        if filled_w > 2:
            draw.rounded_rectangle(
                [bar_x, bar_y, bar_x + filled_w, bar_y + bar_h],
                radius=_BAR_RADIUS,
                fill=fill_rgb,
            )

        # Draw value text inside / below the bar
        pct_text = f"{value:.0f}%"
        font = get_font(max(9, bar_h - 4))
        bbox = font.getbbox(pct_text)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        tx = bar_x + (bar_w - tw) // 2
        ty = bar_y + (bar_h - th) // 2
        # Draw with a subtle shadow for legibility on any bar color
        draw.text((tx + 1, ty + 1), pct_text, fill=(0, 0, 0, 160), font=font)
        draw.text((tx, ty), pct_text, fill=(240, 240, 240), font=font)
