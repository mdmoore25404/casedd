"""Generic two-column table widget renderer.

Renders multiline row payloads as a compact key/value table with one row per
line. Each source line should use the format ``left|right``.

Data source keys consumed:
    - ``<source>`` (str) -- newline-delimited rows in ``left|right`` form
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from PIL import Image, ImageDraw
from PIL.ImageFont import FreeTypeFont, ImageFont

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


@dataclass(frozen=True)
class _Row:
    """One parsed table row."""

    left: str
    right: str


class TableWidget(BaseWidget):
    """Render a compact two-column table with dynamic font scaling."""

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        _state: dict[str, object],
    ) -> None:
        """Paint the table widget onto ``img``.

        Args:
            img: Canvas image.
            rect: Widget bounding box.
            cfg: Widget configuration.
            data: Live data store.
            _state: Per-widget mutable state (unused here).
        """
        fill_background(img, rect, cfg.background)
        inner = content_rect(rect, cfg.padding)
        draw = ImageDraw.Draw(img)

        label_h = 0
        if cfg.label:
            label_h = draw_label(draw, inner, cfg.label, color=(150, 150, 150))

        raw = resolve_value(cfg, data)
        source_text = str(raw) if raw is not None else ""
        rows = _parse_rows(source_text)

        if cfg.max_items is not None and cfg.max_items > 0:
            rows = rows[: cfg.max_items]

        if not rows:
            rows = [_Row(left="—", right="—")]

        avail_w = max(1, inner.w - 2)
        avail_h = max(1, inner.h - label_h)
        font, row_h, left_w, _gap = _fit_font(draw, rows, avail_w, avail_h, cfg.font_size)

        color = parse_color(cfg.color, fallback=(220, 225, 230))
        total_h = row_h * len(rows)
        y = inner.y + label_h + max(0, (avail_h - total_h) // 2)

        left_x = inner.x + 1
        right_x = inner.x + inner.w - 2

        for row in rows:
            left_text = _ellipsize(draw, font, row.left, left_w)
            left_bb = draw.textbbox((0, 0), left_text, font=font)
            left_y = y - int(left_bb[1])
            draw.text((left_x, left_y), left_text, fill=color, font=font)

            right_text = row.right
            right_bb = draw.textbbox((0, 0), right_text, font=font)
            right_w = int(right_bb[2] - right_bb[0])
            right_y = y - int(right_bb[1])
            draw.text((right_x - right_w, right_y), right_text, fill=color, font=font)
            y += row_h


def _parse_rows(source_text: str) -> list[_Row]:
    """Parse a table payload into rows.

    Args:
        source_text: Newline-delimited text rows.

    Returns:
        Parsed rows. Supports ``left|right`` and falls back to splitting on the
        final space for compatibility with older payloads.
    """
    rows: list[_Row] = []
    for raw_line in source_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "|" in line:
            left_raw, right_raw = line.split("|", maxsplit=1)
        else:
            parts = line.rsplit(maxsplit=1)
            if len(parts) == 2:
                left_raw, right_raw = parts
            else:
                left_raw, right_raw = line, ""

        left = _strip_rank_prefix(left_raw.strip())
        right = right_raw.strip()
        if left or right:
            rows.append(_Row(left=left or "—", right=right or "—"))
    return rows


def _strip_rank_prefix(value: str) -> str:
    """Remove leading numeric rank markers like ``1. `` from legacy rows."""
    return re.sub(r"^\s*\d+\.\s+", "", value)


def _fit_font(
    draw: ImageDraw.ImageDraw,
    rows: list[_Row],
    max_w: int,
    max_h: int,
    font_size: int | str,
) -> tuple[FreeTypeFont | ImageFont, int, int, int]:
    """Pick the largest font and row geometry that fit the table box."""
    row_count = max(1, len(rows))
    dynamic_min = max(1, min(max_w, max_h) // 34)
    if font_size == "auto":
        start_size = max(dynamic_min, max_h // row_count)
    else:
        start_size = max(dynamic_min, int(font_size))

    for size in range(start_size, dynamic_min - 1, -1):
        font = get_font(size)
        row_h = _line_height(draw, font, size)
        total_h = row_h * row_count
        if total_h > max_h:
            continue

        right_w = _max_text_width(draw, font, [row.right for row in rows])
        gap = max(2, size // 3)
        left_w = max_w - right_w - gap
        if left_w < max(8, max_w // 4):
            continue
        return font, row_h, left_w, gap

    fallback = get_font(dynamic_min)
    fallback_h = _line_height(draw, fallback, dynamic_min)
    fallback_gap = max(1, dynamic_min // 3)
    fallback_right = _max_text_width(draw, fallback, [row.right for row in rows])
    fallback_left = max(1, max_w - fallback_right - fallback_gap)
    return fallback, fallback_h, fallback_left, fallback_gap


def _line_height(
    draw: ImageDraw.ImageDraw,
    font: FreeTypeFont | ImageFont,
    size_hint: int,
) -> int:
    """Return row height derived from glyph bounds and responsive line gap."""
    line_bb = draw.textbbox((0, 0), "Ag", font=font)
    text_h = int(line_bb[3] - line_bb[1])
    gap = max(1, size_hint // 6)
    return text_h + gap


def _max_text_width(
    draw: ImageDraw.ImageDraw,
    font: FreeTypeFont | ImageFont,
    values: list[str],
) -> int:
    """Measure the widest text in values."""
    width = 0
    for value in values:
        bb = draw.textbbox((0, 0), value, font=font)
        width = max(width, int(bb[2] - bb[0]))
    return width


def _ellipsize(
    draw: ImageDraw.ImageDraw,
    font: FreeTypeFont | ImageFont,
    text: str,
    max_w: int,
) -> str:
    """Trim text with ellipsis so it fits within max_w pixels."""
    if max_w <= 0:
        return ""

    full_bb = draw.textbbox((0, 0), text, font=font)
    if int(full_bb[2] - full_bb[0]) <= max_w:
        return text

    ellipsis = "..."
    ell_bb = draw.textbbox((0, 0), ellipsis, font=font)
    ell_w = int(ell_bb[2] - ell_bb[0])
    if ell_w >= max_w:
        return ""

    current = text
    while current:
        candidate = f"{current}{ellipsis}"
        bb = draw.textbbox((0, 0), candidate, font=font)
        if int(bb[2] - bb[0]) <= max_w:
            return candidate
        current = current[:-1]
    return ""
