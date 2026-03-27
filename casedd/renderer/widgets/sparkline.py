"""Sparkline widget renderer.

Maintains a rolling sample buffer and draws a smooth line chart with no axes.

Example .casedd config:

.. code-block:: yaml

    net_in:
      type: sparkline
      source: net.bytes_recv_rate
      samples: 60
      label: "↓ MB/s"
      color: "#6bcb77"
"""

from __future__ import annotations

from collections import deque

from PIL import Image, ImageDraw

from casedd.data_store import DataStore
from casedd.renderer.color import parse_color
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

_AREA_ALPHA = 60   # alpha of the filled area under the line (0-255)


class SparklineWidget(BaseWidget):
    """Renders a rolling line chart with a filled area underneath."""

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        state: dict[str, object],
    ) -> None:
        """Paint the sparkline onto ``img``.

        Args:
            img: Canvas image.
            rect: Widget bounding box.
            cfg: Widget configuration.
            data: Live data store.
            state: Mutable state dict — stores sample buffer and auto-max.
        """
        fill_background(img, rect, cfg.background)
        inner = content_rect(rect, cfg.padding)
        draw = ImageDraw.Draw(img)

        label_h = 0
        if cfg.label:
            label_h = draw_label(draw, inner, cfg.label, color=(150, 150, 150))

        buf_key = "buf"
        if buf_key not in state:
            state[buf_key] = deque[float](maxlen=cfg.samples)
        buf: deque[float] = state[buf_key]  # type: ignore[assignment]

        raw = resolve_value(cfg, data)
        try:
            value = float(raw) if raw is not None else 0.0
        except (ValueError, TypeError):
            value = 0.0
        buf.append(max(0.0, value))

        if len(buf) < 2:
            return

        area_x = inner.x + 2
        area_y = inner.y + label_h + 2
        area_w = inner.w - 4
        area_h = inner.h - label_h - 4

        # Dynamic max: use the configured max, or auto-scale to buf peak
        data_max = cfg.max if cfg.max > cfg.min else max(*buf, 0.001)
        data_min = cfg.min

        color = parse_color(cfg.color, fallback=(100, 200, 100))

        # Build x,y point list
        points: list[tuple[int, int]] = []
        for i, sample in enumerate(buf):
            x = area_x + int(i * area_w / (cfg.samples - 1))
            ratio = (sample - data_min) / (data_max - data_min) if data_max != data_min else 0.0
            y = area_y + area_h - int(ratio * area_h)
            points.append((x, y))

        # Draw filled polygon (area under curve)
        poly_points = [(area_x, area_y + area_h), *points, (area_x + area_w, area_y + area_h)]
        # Create a temporary RGBA layer for alpha blending the fill
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.polygon(poly_points, fill=(*color, _AREA_ALPHA))
        img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"), (0, 0))

        # Redraw the draw handle after paste (img was modified)
        draw = ImageDraw.Draw(img)

        # Draw the line itself
        draw.line(points, fill=color, width=2)

        # Current value text in top-right corner
        current_str = f"{value:.2f}"
        font = get_font(max(9, area_h // 3))
        bbox = font.getbbox(current_str)
        tw = bbox[2] - bbox[0]
        draw.text((area_x + area_w - tw - 2, area_y + 2), current_str, fill=color, font=font)
