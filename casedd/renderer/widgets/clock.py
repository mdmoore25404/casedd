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
        draw_value_text(draw, inner, time_str, color, cfg.font_size, label_offset=label_h)
