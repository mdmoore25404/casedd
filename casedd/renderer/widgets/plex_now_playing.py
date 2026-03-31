"""Plex now-playing table widget renderer.

Consumes ``plex.sessions.rows`` emitted by :class:`~casedd.getters.plex.PlexGetter`
and renders a compact table of active streams.

Row format expected:
    USER|TITLE|MEDIA_TYPE|PROGRESS_PERCENT|TRANSCODE_DECISION
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
class _NowPlayingRow:
    """One parsed now-playing row."""

    user: str
    title: str
    media_type: str
    progress_percent: float
    decision: str


class PlexNowPlayingWidget(BaseWidget):
    """Render active Plex sessions in a compact table."""

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        _state: dict[str, object],
    ) -> None:
        """Paint now-playing rows within the allocated rectangle."""
        fill_background(img, rect, cfg.background)
        inner = content_rect(rect, cfg.padding)
        draw = ImageDraw.Draw(img)

        title = cfg.label if cfg.label else "Now Playing"
        label_h = draw_label(draw, inner, title, color=(150, 150, 150))

        source = cfg.source.strip() if cfg.source else "plex.sessions.rows"
        rows_raw = data.get(source)
        rows = _parse_rows(str(rows_raw) if rows_raw is not None else "")

        if cfg.filter_regex:
            try:
                rx = re.compile(cfg.filter_regex)
                rows = [r for r in rows if not rx.search(f"{r.user}|{r.title}")]
            except re.error:
                pass

        accent = parse_color(cfg.color, fallback=(109, 204, 133))
        body_size = (
            cfg.font_size
            if isinstance(cfg.font_size, int)
            else max(10, min(24, inner.h // 12))
        )
        body_font = get_font(body_size)
        head_font = get_font(body_size + 1)
        row_bb = draw.textbbox((0, 0), "Ag", font=body_font)
        row_h = int(row_bb[3] - row_bb[1]) + 3

        left = inner.x + 4
        avail_w = inner.w - 8
        user_right = left + int(avail_w * 0.18)
        title_left = left + int(avail_w * 0.20)
        mode_left = left + int(avail_w * 0.74)
        prog_right = left + int(avail_w * 0.98)

        y = inner.y + label_h + 4
        _draw_header(
            draw,
            head_font,
            y,
            left,
            user_right,
            title_left,
            mode_left,
            prog_right,
        )
        y += row_h

        if not rows:
            draw.text(
                (title_left, y),
                "No active sessions",
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
                user_right,
                title_left,
                mode_left,
                prog_right,
                accent,
            )
            y += row_h


def _draw_header(  # noqa: PLR0913 -- explicit column positions keep draw path fast
    draw: ImageDraw.ImageDraw,
    font: FreeTypeFont | ImageFont,
    y: int,
    left: int,
    user_right: int,
    title_left: int,
    mode_left: int,
    prog_right: int,
) -> None:
    """Draw now-playing table headers."""
    color = (188, 196, 208)
    _draw_right(draw, font, y, user_right, "USER", color)
    draw.text((title_left, y), "TITLE", fill=color, font=font)
    draw.text((mode_left, y), "MODE", fill=color, font=font)
    _draw_right(draw, font, y, prog_right, "%", color)
    draw.line((left, y + 16, prog_right, y + 16), fill=(52, 62, 72), width=1)


def _draw_row(  # noqa: PLR0913 -- explicit table geometry is clearer than packing dicts
    draw: ImageDraw.ImageDraw,
    font: FreeTypeFont | ImageFont,
    y: int,
    row: _NowPlayingRow,
    user_right: int,
    title_left: int,
    mode_left: int,
    prog_right: int,
    accent: tuple[int, int, int],
) -> None:
    """Draw one now-playing row."""
    decision_color = _decision_color(row.decision, accent)
    _draw_right(draw, font, y, user_right, row.user[:12], (205, 210, 218))
    draw.text((title_left, y), row.title[:32], fill=(225, 228, 233), font=font)
    draw.text((mode_left, y), row.decision[:12], fill=decision_color, font=font)
    _draw_right(draw, font, y, prog_right, f"{row.progress_percent:.0f}", (193, 200, 210))


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


def _decision_color(
    decision: str,
    accent: tuple[int, int, int],
) -> tuple[int, int, int]:
    """Map decision label to row color."""
    mode_key = decision.strip().lower()
    if mode_key == "transcode":
        return (255, 135, 135)
    if mode_key == "direct_stream":
        return (255, 210, 120)
    return accent


def _parse_rows(text: str) -> list[_NowPlayingRow]:
    """Parse pipe-delimited now-playing rows."""
    rows: list[_NowPlayingRow] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split("|", maxsplit=4)
        if len(parts) != 5:
            continue
        try:
            progress = float(parts[3])
        except ValueError:
            progress = 0.0
        rows.append(
            _NowPlayingRow(
                user=parts[0],
                title=parts[1],
                media_type=parts[2],
                progress_percent=max(0.0, min(100.0, progress)),
                decision=parts[4],
            )
        )
    return rows
