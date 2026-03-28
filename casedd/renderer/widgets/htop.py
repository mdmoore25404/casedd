"""Htop-like process table widget renderer.

Renders structured process rows emitted by
:class:`~casedd.getters.htop.HtopGetter` as a properly aligned table with
right-justified numeric columns and an active-sort indicator (▲).

The renderer reads ``cfg.sort_key`` ("cpu" or "mem") to determine the sort
column and draws a yellow ▲ next to that column header.  Column widths and
font sizes scale to the widget's allocated bounding rectangle.

Data source keys consumed:
    - ``{prefix}.rows``    (str) -- pipe-delimited: ``PID|CPU|MEM|NAME``
    - ``{prefix}.summary`` (str) -- footer summary line
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw
from PIL.ImageFont import FreeTypeFont, ImageFont

from casedd.data_store import DataStore
from casedd.renderer.color import parse_color
from casedd.renderer.fonts import get_font
from casedd.renderer.widgets.base import BaseWidget, content_rect, draw_label, fill_background
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig


@dataclass(frozen=True)
class _ProcEntry:
    """Parsed process data for one table row."""

    pid: int
    cpu: float
    mem: float
    name: str


@dataclass(frozen=True)
class _ColLayout:
    """Horizontal column boundaries for the process table."""

    left: int
    pid_right: int
    cpu_right: int
    mem_right: int
    name_left: int
    name_right: int


@dataclass(frozen=True)
class _DrawCtx:
    """Bundled drawing context passed to table helper functions."""

    draw: ImageDraw.ImageDraw
    font: FreeTypeFont | ImageFont


@dataclass(frozen=True)
class _RightDraw:
    """Helper for right-aligning a text cell to a given x boundary."""

    draw: ImageDraw.ImageDraw
    font: FreeTypeFont | ImageFont
    y: int

    def emit(self, right_x: int, text: str, fill: tuple[int, int, int]) -> None:
        """Draw text right-aligned so its right edge ends at right_x.

        Args:
            right_x: The x-coordinate of the right edge of the cell.
            text: The string to draw.
            fill: RGB colour tuple.
        """
        bbox = self.draw.textbbox((0, 0), text, font=self.font)
        self.draw.text((right_x - (bbox[2] - bbox[0]), self.y), text, fill=fill, font=self.font)


class HtopWidget(BaseWidget):
    """Render a compact process table sorted by CPU or MEM usage."""

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        _state: dict[str, object],
    ) -> None:
        """Paint the htop-like process table onto img within rect.

        Args:
            img: Canvas image, modified in-place.
            rect: Allocated bounding box for this widget.
            cfg: Widget configuration (source, color, font_size, sort_key, ...).
            data: Live data store.
            _state: Per-widget state dict (unused for this renderer).
        """
        fill_background(img, rect, cfg.background)
        inner = content_rect(rect, cfg.padding)
        draw_ctx = ImageDraw.Draw(img)

        title = cfg.label if cfg.label else "Top Processes"
        label_h = draw_label(draw_ctx, inner, title, color=(150, 150, 150))

        source = cfg.source.strip() if cfg.source else "htop"
        prefix = source.removesuffix(".rows").rstrip(".")

        rows_raw = data.get(f"{prefix}.rows")
        entries = _parse_rows(str(rows_raw) if rows_raw is not None else "")

        sort_by = cfg.sort_key.lower() if cfg.sort_key else "cpu"
        if sort_by == "mem":
            entries.sort(key=lambda e: (e.mem, e.cpu), reverse=True)
        else:
            entries.sort(key=lambda e: (e.cpu, e.mem), reverse=True)

        accent = parse_color(cfg.color, fallback=(96, 210, 132))
        body_sz = (
            cfg.font_size if isinstance(cfg.font_size, int) else max(10, min(16, inner.h // 17))
        )
        body_font = get_font(body_sz)
        header_font = get_font(body_sz + 1)

        sample_bb = draw_ctx.textbbox((0, 0), "Ag", font=body_font)
        row_h = int(sample_bb[3] - sample_bb[1]) + 3

        avail_w = inner.w - 8
        offset = inner.x + 4
        col = _ColLayout(
            left=offset,
            pid_right=offset + int(avail_w * 0.10),
            cpu_right=offset + int(avail_w * 0.23),
            mem_right=offset + int(avail_w * 0.36),
            name_left=offset + int(avail_w * 0.36) + 8,
            name_right=inner.x + inner.w - 4,
        )

        y = inner.y + label_h + 4
        body_ctx = _DrawCtx(draw=draw_ctx, font=body_font)
        header_ctx = _DrawCtx(draw=draw_ctx, font=header_font)
        y = _paint_header(header_ctx, y, col, sort_by, inner.x + inner.w - 4)

        footer_h = row_h + 2
        max_visible = max(1, (inner.y + inner.h - y - footer_h) // row_h)
        for entry in entries[:max_visible]:
            if y + row_h > inner.y + inner.h - footer_h:
                break
            _paint_row(body_ctx, y, col, entry, accent)
            y += row_h

        summary_raw = data.get(f"{prefix}.summary")
        if summary_raw is not None:
            footer_y = inner.y + inner.h - row_h
            note_font = get_font(max(9, body_sz - 1))
            draw_ctx.text((col.left, footer_y), str(summary_raw), fill=accent, font=note_font)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _paint_header(
    ctx: _DrawCtx,
    y: int,
    col: _ColLayout,
    sort_by: str,
    right_edge: int,
) -> int:
    """Draw the column header row and separator; return y after the separator.

    Args:
        ctx: Drawing context (draw + font).
        y: Top y-coordinate for the header row.
        col: Column boundary layout.
        sort_by: Active sort key ("cpu" or "mem").
        right_edge: Right edge x for the separator line.

    Returns:
        Updated y position after the separator line.
    """
    hdr_color: tuple[int, int, int] = (185, 195, 205)
    sort_color: tuple[int, int, int] = (255, 220, 100)

    cpu_label = "CPU% \u25b2" if sort_by == "cpu" else "CPU%"
    mem_label = "MEM% \u25b2" if sort_by == "mem" else "MEM%"

    rd = _RightDraw(draw=ctx.draw, font=ctx.font, y=y)
    rd.emit(col.pid_right, "PID", hdr_color)
    rd.emit(col.cpu_right, cpu_label, sort_color if sort_by == "cpu" else hdr_color)
    rd.emit(col.mem_right, mem_label, sort_color if sort_by == "mem" else hdr_color)
    ctx.draw.text((col.name_left, y), "NAME", fill=hdr_color, font=ctx.font)

    hdr_bb = ctx.draw.textbbox((0, 0), "Ag", font=ctx.font)
    y += int(hdr_bb[3] - hdr_bb[1]) + 3
    ctx.draw.line((col.left, y, right_edge, y), fill=(55, 65, 75), width=1)
    return y + 3


def _paint_row(
    ctx: _DrawCtx,
    y: int,
    col: _ColLayout,
    entry: _ProcEntry,
    accent: tuple[int, int, int],
) -> None:
    """Draw one process row with right-aligned numeric columns.

    Args:
        ctx: Drawing context (draw + font).
        y: Top y-coordinate for this row.
        col: Column boundary layout.
        entry: Parsed process data.
        accent: Accent colour used for high-CPU processes.
    """
    rd = _RightDraw(draw=ctx.draw, font=ctx.font, y=y)
    rd.emit(col.pid_right, str(entry.pid), (160, 170, 180))
    cpu_fill = accent if entry.cpu >= 5.0 else (200, 210, 220)
    rd.emit(col.cpu_right, f"{entry.cpu:.1f}%", cpu_fill)
    rd.emit(col.mem_right, f"{entry.mem:.1f}%", (200, 210, 220))

    max_name_w = col.name_right - col.name_left
    name = entry.name
    while name:
        bbox = ctx.draw.textbbox((0, 0), name, font=ctx.font)
        if bbox[2] - bbox[0] <= max_name_w:
            break
        name = name[:-1]
    ctx.draw.text((col.name_left, y), name, fill=(220, 225, 230), font=ctx.font)


def _parse_rows(text: str) -> list[_ProcEntry]:
    """Parse pipe-delimited process rows emitted by the htop getter.

    Line format: PID|CPU|MEM|NAME

    Args:
        text: Raw multi-line string from the data store.

    Returns:
        List of parsed _ProcEntry objects.
    """
    result: list[_ProcEntry] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split("|", maxsplit=3)
        if len(parts) < 4:
            continue
        try:
            pid = int(parts[0])
            cpu = float(parts[1])
            mem = float(parts[2])
        except ValueError:
            continue
        result.append(_ProcEntry(pid=pid, cpu=cpu, mem=mem, name=parts[3]))
    return result
