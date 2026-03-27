"""Gauge widget renderer.

Draws a tachometer-style arc gauge with a colored sweep and needle, using
PIL's arc drawing primitives. Supports ``color_stops`` for dynamic colorization.

Example .casedd config:

.. code-block:: yaml

    cpu:
      type: gauge
      source: cpu.percent
      label: "CPU"
      min: 0
      max: 100
      arc_start: 225
      arc_end: -45
      color_stops:
        - [0,  "#6bcb77"]
        - [70, "#ffd93d"]
        - [90, "#ff6b6b"]
"""

from __future__ import annotations

import math

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

_TRACK_COLOR = (50, 50, 50)
_ARC_WIDTH_RATIO = 0.12   # arc stroke width as a fraction of radius


class GaugeWidget(BaseWidget):
    """Renders a tachometer-style arc gauge.

    The arc sweeps from ``arc_start`` to ``arc_end`` degrees (PIL convention:
    0° = 3 o'clock, angles increase clockwise). The default 225° → 315°
    (going clockwise through the bottom) gives the classic tachometer look.
    """

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        _state: dict[str, object],
    ) -> None:
        """Paint the gauge onto ``img``.

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

        # Resolve value
        raw = resolve_value(cfg, data)
        try:
            value = float(raw) if raw is not None else cfg.min
        except (ValueError, TypeError):
            value = cfg.min
        value = max(cfg.min, min(cfg.max, value))

        # Determine fill color
        if cfg.color_stops:
            fill_rgb = interpolate_color_stops(value, cfg.color_stops, cfg.min, cfg.max)
        else:
            fill_rgb = parse_color(cfg.color, fallback=(70, 130, 200))

        # Calculate gauge bounding circle
        available_h = inner.h - label_h
        size = min(inner.w, available_h)
        cx = inner.x + inner.w // 2
        cy = inner.y + label_h + available_h // 2
        radius = max(4, size // 2 - 4)
        arc_w = max(3, int(radius * _ARC_WIDTH_RATIO))

        box = [cx - radius, cy - radius, cx + radius, cy + radius]

        # Convert our degree convention to PIL's (PIL: 0=east, CW; CSS: 0=east)
        # arc_start/arc_end are in "standard math" degrees where 0=east, CCW.
        # PIL arc: 0=east, increasing angle = clockwise.
        # We use the CSS convention: just pass through to PIL directly.
        start_deg = cfg.arc_start   # e.g. 225
        end_deg = cfg.arc_end       # e.g. -45 (equivalent to 315)

        # Arc sweep: in PIL, arc(start, end) draws CW from start to end
        # Compute the value angle within [start_deg, end_deg] span
        total_sweep = (end_deg - start_deg) % 360 or 360
        span = cfg.max - cfg.min
        ratio = (value - cfg.min) / span if span > 0 else 0.0
        value_deg = start_deg + total_sweep * ratio

        # Draw track (unfilled arc)
        draw.arc(box, start=start_deg, end=end_deg, fill=_TRACK_COLOR, width=arc_w)

        # Draw filled arc
        if ratio > 0.001:
            draw.arc(box, start=start_deg, end=value_deg, fill=fill_rgb, width=arc_w)

        # Draw needle line from center to arc edge at value angle
        needle_angle_rad = math.radians(value_deg)
        inner_r = radius - arc_w - 2
        nx = cx + int(inner_r * math.cos(needle_angle_rad))
        ny = cy + int(inner_r * math.sin(needle_angle_rad))
        draw.line([(cx, cy), (nx, ny)], fill=fill_rgb, width=2)

        # Center dot
        dot_r = max(3, arc_w // 2)
        draw.ellipse(
            [cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r],
            fill=fill_rgb,
        )

        # Value text below center
        val_str = f"{value:.0f}"
        font = get_font(max(10, size // 5))
        bbox = font.getbbox(val_str)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text(
            (cx - tw // 2, cy + radius // 3 - th // 2),
            val_str,
            fill=(220, 220, 220),
            font=font,
        )
