"""Network listening ports table widget renderer.

Renders the structured port table produced by
:class:`~casedd.getters.net_ports.NetPortsGetter` as a four-column table:

    PROTO | PORT | ADDRESS | PROCESS

Column widths and font sizes scale to the widget's allocated bounding
rectangle so the widget composes cleanly alongside other widgets.

Data source keys consumed:
    - ``{prefix}.rows``       (str)   -- pipe-delimited port rows
    - ``{prefix}.port_count`` (float) -- total number of entries for footer
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
class _PortEntry:
    """Parsed data for one port table row."""

    proto: str
    port: int
    addr: str
    pid: str
    name: str


@dataclass(frozen=True)
class _ColLayout:
    """Horizontal column boundaries for the ports table."""

    left: int
    proto_right: int
    port_right: int
    addr_left: int
    name_left: int
    name_right: int


@dataclass(frozen=True)
class _DrawCtx:
    """Bundled drawing context (draw surface + current font)."""

    draw: ImageDraw.ImageDraw
    font: FreeTypeFont | ImageFont


@dataclass(frozen=True)
class _TableCtx:
    """Bundled table geometry passed to row helpers."""

    col: _ColLayout
    inner: Rect
    row_h: int


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


class NetPortsWidget(BaseWidget):
    """Render a netstat-style listening ports table."""

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        _state: dict[str, object],
    ) -> None:
        """Paint the listening ports table onto img within rect.

        Args:
            img: Canvas image, modified in-place.
            rect: Allocated bounding box for this widget.
            cfg: Widget configuration (source, color, font_size, ...).
            data: Live data store.
            _state: Per-widget state dict (unused).
        """
        fill_background(img, rect, cfg.background)
        inner = content_rect(rect, cfg.padding)
        draw = ImageDraw.Draw(img)

        title = cfg.label if cfg.label else "Listening Ports"
        label_h = draw_label(draw, inner, title, color=(150, 150, 150))

        source = cfg.source.strip() if cfg.source else "netports"
        prefix = source.removesuffix(".rows").rstrip(".")

        rows_raw = data.get(f"{prefix}.rows")
        entries = _parse_rows(str(rows_raw) if rows_raw is not None else "")
        accent = parse_color(cfg.color, fallback=(96, 180, 230))

        body_sz = (
            cfg.font_size if isinstance(cfg.font_size, int) else max(10, min(16, inner.h // 17))
        )
        body_font = get_font(body_sz)
        header_font = get_font(body_sz + 1)
        sample_bb = draw.textbbox((0, 0), "Ag", font=body_font)
        row_h = int(sample_bb[3] - sample_bb[1]) + 3

        avail_w = inner.w - 8
        offset = inner.x + 4
        col = _ColLayout(
            left=offset,
            proto_right=offset + int(avail_w * 0.10),
            port_right=offset + int(avail_w * 0.23),
            addr_left=offset + int(avail_w * 0.25),
            name_left=offset + int(avail_w * 0.57),
            name_right=inner.x + inner.w - 4,
        )
        tbl = _TableCtx(col=col, inner=inner, row_h=row_h)
        right_edge = inner.x + inner.w - 4

        hdr_ctx = _DrawCtx(draw=draw, font=header_font)
        body_ctx = _DrawCtx(draw=draw, font=body_font)

        y = inner.y + label_h + 4
        y = _paint_header(hdr_ctx, y, col, right_edge)
        y = _paint_rows(body_ctx, y, tbl, entries, accent)

        count_raw = data.get(f"{prefix}.port_count")
        if count_raw is not None:
            count = int(float(str(count_raw)))
            footer_y = inner.y + inner.h - row_h
            if y <= footer_y:
                word = "port" if count == 1 else "ports"
                note = get_font(max(9, body_sz - 1))
                draw.text(
                    (col.left, footer_y),
                    f"{count} listening {word}",
                    fill=accent,
                    font=note,
                )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _paint_header(
    ctx: _DrawCtx,
    y: int,
    col: _ColLayout,
    right_edge: int,
) -> int:
    """Draw the header row and separator; return y after the separator.

    Args:
        ctx: Drawing context (draw + font).
        y: Top y-coordinate for the header.
        col: Column boundary layout.
        right_edge: Right x for the separator line.

    Returns:
        Updated y position after the separator line.
    """
    hdr_color: tuple[int, int, int] = (185, 195, 205)
    rd = _RightDraw(draw=ctx.draw, font=ctx.font, y=y)
    rd.emit(col.proto_right, "PROTO", hdr_color)
    rd.emit(col.port_right, "PORT", hdr_color)
    ctx.draw.text((col.addr_left, y), "ADDRESS", fill=hdr_color, font=ctx.font)
    ctx.draw.text((col.name_left, y), "PROCESS", fill=hdr_color, font=ctx.font)
    hdr_bb = ctx.draw.textbbox((0, 0), "Ag", font=ctx.font)
    y += int(hdr_bb[3] - hdr_bb[1]) + 3
    ctx.draw.line((col.left, y, right_edge, y), fill=(55, 65, 75), width=1)
    return y + 3


def _paint_rows(
    ctx: _DrawCtx,
    y: int,
    tbl: _TableCtx,
    entries: list[_PortEntry],
    accent: tuple[int, int, int],
) -> int:
    """Draw all data rows; return y after the last drawn row.

    Args:
        ctx: Drawing context (draw + font).
        y: Starting y position.
        tbl: Table geometry (column layout, inner rect, row height).
        entries: Parsed port entries to render.
        accent: Accent colour for TCP entries.

    Returns:
        y position after the last drawn row.
    """
    for entry in entries:
        if y + tbl.row_h > tbl.inner.y + tbl.inner.h:
            break
        _paint_row(ctx, y, tbl.col, entry, accent)
        y += tbl.row_h
    return y


def _paint_row(
    ctx: _DrawCtx,
    y: int,
    col: _ColLayout,
    entry: _PortEntry,
    accent: tuple[int, int, int],
) -> None:
    """Draw one port row with right-aligned PROTO and PORT columns.

    Args:
        ctx: Drawing context (draw + font).
        y: Top y-coordinate for this row.
        col: Column boundary layout.
        entry: Parsed port data.
        accent: Accent colour for TCP entries.
    """
    proto_fill: tuple[int, int, int] = accent if entry.proto == "TCP" else (200, 180, 100)
    rd = _RightDraw(draw=ctx.draw, font=ctx.font, y=y)
    rd.emit(col.proto_right, entry.proto, proto_fill)
    rd.emit(col.port_right, str(entry.port), (200, 210, 220))
    ctx.draw.text((col.addr_left, y), entry.addr[:20], fill=(180, 190, 200), font=ctx.font)

    max_name_w = col.name_right - col.name_left
    name = entry.name
    while name:
        bbox = ctx.draw.textbbox((0, 0), name, font=ctx.font)
        if bbox[2] - bbox[0] <= max_name_w:
            break
        name = name[:-1]
    ctx.draw.text((col.name_left, y), name, fill=(220, 225, 230), font=ctx.font)


def _parse_rows(text: str) -> list[_PortEntry]:
    """Parse pipe-delimited port rows from the getter payload.

    Line format: PROTO|PORT|ADDR|PID|NAME

    Args:
        text: Raw multi-line string from the data store.

    Returns:
        List of _PortEntry objects.
    """
    result: list[_PortEntry] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split("|", maxsplit=4)
        if len(parts) < 5:
            continue
        try:
            port = int(parts[1])
        except ValueError:
            continue
        result.append(
            _PortEntry(proto=parts[0], port=port, addr=parts[2], pid=parts[3], name=parts[4])
        )
    return result
