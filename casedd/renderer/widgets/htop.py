"""Htop-like process table widget renderer."""

from __future__ import annotations

from PIL import Image, ImageDraw

from casedd.data_store import DataStore
from casedd.renderer.color import parse_color
from casedd.renderer.fonts import get_font
from casedd.renderer.widgets.base import BaseWidget, content_rect, draw_label, fill_background
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig


class HtopWidget(BaseWidget):
    """Render a compact process table sorted by CPU usage."""

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        _state: dict[str, object],
    ) -> None:
        """Paint htop-like process rows."""
        fill_background(img, rect, cfg.background)
        inner = content_rect(rect, cfg.padding)
        draw = ImageDraw.Draw(img)

        title = cfg.label if cfg.label else "Top Processes"
        label_h = draw_label(draw, inner, title, color=(150, 150, 150))

        source = cfg.source.strip() if cfg.source else "htop.rows"
        if source.endswith(".rows"):
            rows_key = source
            prefix = source[: -len(".rows")]
        else:
            rows_key = f"{source}.rows"
            prefix = source

        rows_raw = data.get(rows_key)
        rows_text = str(rows_raw) if rows_raw is not None else ""
        rows = [line for line in rows_text.splitlines() if line.strip()]

        header = "PID      CPU%   MEM%   NAME"
        header_font = get_font(max(11, inner.h // 18))
        body_font = get_font(max(10, inner.h // 23))
        accent = parse_color(cfg.color, fallback=(96, 210, 132))

        text_x = inner.x + 4
        y = inner.y + label_h + 4
        draw.text((text_x, y), header, fill=(185, 195, 205), font=header_font)
        header_h = int(header_font.getbbox("Ag")[3] - header_font.getbbox("Ag")[1])
        y += header_h + 3
        draw.line((text_x, y, inner.x + inner.w - 4, y), fill=(55, 65, 75), width=1)
        y += 4

        max_lines = int(max(1, (inner.y + inner.h - y) // 14))
        shown = rows[:max_lines]
        for line in shown:
            draw.text((text_x, y), line, fill=(220, 225, 230), font=body_font)
            body_h = int(body_font.getbbox("Ag")[3] - body_font.getbbox("Ag")[1])
            y += body_h + 2

        summary = data.get(f"{prefix}.summary")
        if summary is not None and y + 14 < inner.y + inner.h:
            draw.text((text_x, inner.y + inner.h - 14), str(summary), fill=accent, font=body_font)
