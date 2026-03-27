"""Clock widget renderer.

Displays the current local time formatted via a strftime format string.
Re-rendered fresh on every frame (no caching needed).

Example .casedd config:

.. code-block:: yaml

    time:
      type: clock
      format: "%H:%M:%S"
      color: "#ffffff"
      font_size: 32
"""

from __future__ import annotations

import time

from PIL import Image, ImageDraw

from casedd.data_store import DataStore
from casedd.renderer.color import parse_color
from casedd.renderer.fonts import fit_font, get_font
from casedd.renderer.widgets.base import (
    BaseWidget,
    content_rect,
    draw_label,
    draw_value_text,
    fill_background,
)
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig


class ClockWidget(BaseWidget):
    """Renders the current local time using a strftime format string."""

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        _data: DataStore,
        _state: dict[str, object],
    ) -> None:
        """Paint the clock onto ``img``.

        Args:
            img: Canvas image.
            rect: Widget bounding box.
            cfg: Widget configuration (uses ``cfg.format`` for strftime).
            _data: Unused -- clock reads system time directly.
            _state: Unused for this widget type.
        """
        fill_background(img, rect, cfg.background)
        inner = content_rect(rect, cfg.padding)
        draw = ImageDraw.Draw(img)
        color = parse_color(cfg.color, fallback=(220, 220, 220))

        label_h = 0
        if cfg.label:
            label_h = draw_label(draw, inner, cfg.label, color=(150, 150, 150))

        time_str = time.strftime(cfg.format)
        if "\n" not in time_str:
            draw_value_text(draw, inner, time_str, color, cfg.font_size, label_offset=label_h)
            return

        available_h = inner.h - label_h
        available_w = inner.w - 8

        if cfg.font_size == "auto":
            longest_line = max(time_str.splitlines(), key=len, default="")
            font = fit_font(longest_line, available_w, max(8, available_h // 2 - 4))
        else:
            font = get_font(int(cfg.font_size))

        bbox = draw.multiline_textbbox((0, 0), time_str, font=font, spacing=2, align="center")
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = inner.x + (inner.w - tw) // 2
        y = inner.y + label_h + max(0, (available_h - th) // 2)
        draw.multiline_text((x, y), time_str, fill=color, font=font, spacing=2, align="center")
