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
from casedd.renderer.color import interpolate_color_stops, parse_color
from casedd.renderer.widgets.base import (
    BaseWidget,
    content_rect,
    draw_label,
    draw_value_text,
    fill_background,
    resolve_value,
)
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig


def _status_color(raw_value: object, default_color: tuple[int, int, int]) -> tuple[int, int, int]:
    """Resolve a semantic color for status-like string values."""
    if not isinstance(raw_value, str):
        return default_color
    normalized = raw_value.strip().lower()
    if not normalized:
        return default_color
    if normalized in {"healthy", "online", "ok", "running", "up-to-date", "up to date"}:
        return (124, 222, 156)
    if normalized in {"degraded", "warning", "warn", "pending", "available"}:
        return (239, 192, 88)
    if normalized in {"faulted", "offline", "failed", "down", "critical", "error"}:
        return (234, 107, 107)
    return default_color


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
        inner = content_rect(rect, cfg.padding)
        draw = ImageDraw.Draw(img)
        color = parse_color(cfg.color, fallback=(220, 220, 220))

        label_h = 0
        if cfg.label:
            label_h = draw_label(draw, inner, cfg.label, color=(150, 150, 150))

        raw = resolve_value(cfg, data)
        numeric_value: float | None = None
        if raw is None:
            display = "--"
        elif isinstance(raw, float):
            numeric_value = raw
            display = f"{raw:.{cfg.precision}f}"
        else:
            try:
                numeric_value = float(raw)
            except (TypeError, ValueError):
                numeric_value = None
            display = str(raw)

        if cfg.color_stops and numeric_value is not None:
            color = interpolate_color_stops(numeric_value, cfg.color_stops, cfg.min, cfg.max)
        elif isinstance(raw, str):
            color = _status_color(raw, color)

        if cfg.unit:
            display = f"{display}{cfg.unit}"

        draw_value_text(draw, inner, display, color, cfg.font_size, label_offset=label_h)
