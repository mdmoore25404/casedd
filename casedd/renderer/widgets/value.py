"""Value widget renderer.

Displays a numeric value (optionally with a label and unit) scaled to fill
its bounding box when ``font_size: auto``.

Example .casedd config:

.. code-block:: yaml

    cpu_temp:
      type: value
      source: cpu.temperature
      label: "CPU Temp"
      unit: "°C"
      precision: 1
      color: "#ff6b6b"
      font_size: auto
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from casedd.data_store import DataStore
from casedd.renderer.color import parse_color
from casedd.renderer.widgets.base import (
    BaseWidget,
    draw_label,
    draw_value_text,
    fill_background,
    resolve_value,
)
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig


class ValueWidget(BaseWidget):
    """Renders a numeric value centered in its bounding box.

    Supports optional label, unit suffix, decimal precision, and auto
    font scaling.
    """

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        _state: dict[str, object],
    ) -> None:
        """Paint the value widget onto ``img``.

        Args:
            img: Canvas image.
            rect: Widget bounding box.
            cfg: Widget configuration.
            data: Live data store.
            _state: Unused for this widget type.
        """
        fill_background(img, rect, cfg.background)
        draw = ImageDraw.Draw(img)
        color = parse_color(cfg.color, fallback=(220, 220, 220))

        label_h = 0
        if cfg.label:
            label_h = draw_label(draw, rect, cfg.label, color=(150, 150, 150))

        raw = resolve_value(cfg, data)
        if raw is None:
            display = "--"
        elif isinstance(raw, float):
            display = f"{raw:.{cfg.precision}f}"
        else:
            display = str(raw)

        if cfg.unit:
            display = f"{display}{cfg.unit}"

        draw_value_text(draw, rect, display, color, cfg.font_size, label_offset=label_h)
