"""Plex recently-added table widget renderer.

Consumes ``plex.recently_added.rows`` emitted by
:class:`~casedd.getters.plex.PlexGetter` and renders recent library arrivals.

Row format expected:
    MEDIA_TYPE|LIBRARY|TITLE
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
)
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig


@dataclass(frozen=True)
class _RecentRow:
    """One parsed recently-added row."""

    media_type: str
    library: str
    title: str


class PlexRecentlyAddedWidget(BaseWidget):
    """Render recently-added Plex media rows."""

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        _state: dict[str, object],
    ) -> None:
        """Paint recently-added rows within the allocated rectangle."""
        fill_background(img, rect, cfg.background)
        inner = content_rect(rect, cfg.padding)
        draw = ImageDraw.Draw(img)

        title = cfg.label if cfg.label else "Recently Added"
        label_h = draw_label(draw, inner, title, color=(150, 150, 150))

        source = cfg.source.strip() if cfg.source else "plex.recently_added.rows"
        rows_raw = data.get(source)
        rows = _parse_rows(str(rows_raw) if rows_raw is not None else "")

        if cfg.filter_regex:
            try:
                rx = re.compile(cfg.filter_regex)
                rows = [r for r in rows if not rx.search(f"{r.library}|{r.title}")]
            except re.error:
                pass

        accent = parse_color(cfg.color, fallback=(114, 170, 237))
        body_size = (
            cfg.font_size
            if isinstance(cfg.font_size, int)
            else max(12, min(inner.h // 16, inner.w // 34))
        )
        body_font = get_font(body_size)
        head_font = get_font(max(12, int(body_size * 1.05)))
        row_bb = draw.textbbox((0, 0), "Ag", font=body_font)
        row_h = int(row_bb[3] - row_bb[1]) + max(4, body_size // 4)

        left = inner.x + 4
        avail_w = inner.w - 8
        type_right = left + int(avail_w * 0.20)
        library_left = left + int(avail_w * 0.22)
        title_left = left + int(avail_w * 0.48)
        right = left + avail_w

        y = inner.y + label_h + 4
        _draw_header(
            draw,
            head_font,
            y,
            left,
            type_right,
            library_left,
            title_left,
            right,
            row_h,
        )
        y += row_h

        if not rows:
            draw.text(
                (library_left, y),
                "No recent additions",
                fill=(180, 185, 190),
                font=body_font,
            )
            return

        for row in rows:
            if y + row_h > inner.y + inner.h:
                break
            _draw_row(
                draw,
                body_font,
                y,
                row,
                type_right,
                library_left,
                title_left,
                accent,
            )
            y += row_h


def _draw_header(  # noqa: PLR0913 -- explicit column positions keep draw path fast
    draw: ImageDraw.ImageDraw,
    font: FreeTypeFont | ImageFont,
    y: int,
    left: int,
    type_right: int,
    library_left: int,
    title_left: int,
    right: int,
    row_h: int,
) -> None:
    """Draw recently-added table header row."""
    color = (188, 196, 208)
    _draw_right(draw, font, y, type_right, "TYPE", color)
    draw.text((library_left, y), "LIBRARY", fill=color, font=font)
    draw.text((title_left, y), "TITLE", fill=color, font=font)
    underline_y = y + max(12, row_h - 2)
    draw.line((left, underline_y, right, underline_y), fill=(52, 62, 72), width=1)


def _draw_row(  # noqa: PLR0913 -- explicit table geometry is clearer than packing dicts
    draw: ImageDraw.ImageDraw,
    font: FreeTypeFont | ImageFont,
    y: int,
    row: _RecentRow,
    type_right: int,
    library_left: int,
    title_left: int,
    accent: tuple[int, int, int],
) -> None:
    """Draw one recently-added row."""
    _draw_right(draw, font, y, type_right, row.media_type[:10], accent)
    draw.text((library_left, y), row.library[:16], fill=(194, 201, 210), font=font)
    draw.text((title_left, y), row.title[:28], fill=(225, 228, 233), font=font)


def _draw_right(  # noqa: PLR0913 -- compact helper for right-aligned cells
    draw: ImageDraw.ImageDraw,
    font: FreeTypeFont | ImageFont,
    y: int,
    right_x: int,
    text: str,
    fill: tuple[int, int, int],
) -> None:
    """Draw right-aligned text ending at right_x."""
    bb = draw.textbbox((0, 0), text, font=font)
    draw.text((right_x - int(bb[2] - bb[0]), y), text, fill=fill, font=font)


def _parse_rows(text: str) -> list[_RecentRow]:
    """Parse pipe-delimited recently-added rows."""
    rows: list[_RecentRow] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split("|", maxsplit=2)
        if len(parts) != 3:
            continue
        rows.append(_RecentRow(media_type=parts[0], library=parts[1], title=parts[2]))
    return rows
