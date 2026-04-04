"""Generic two-column table widget renderer.

Renders multiline row payloads as a compact key/value table with one row per
line. Each source line should use the format ``left|right``.

Data source keys consumed:
    - ``<source>`` (str) -- newline-delimited rows in ``left|right`` form
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import time

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


@dataclass(frozen=True)
class _PreparedRow:
    """One measured/render-ready row."""

    left: str
    right: str
    right_width: int


@dataclass(frozen=True)
class _PreparedLayout:
    """Cached table layout for a specific content+rect configuration."""

    font: FreeTypeFont | ImageFont
    row_h: int
    left_w: int
    rows: tuple[_PreparedRow, ...]


@dataclass(frozen=True)
class _LayoutSpec:
    """Inputs that control table layout fitting."""

    max_w: int
    max_h: int
    font_size: int | str
    fit_text: bool
    expected_rows: int
    max_font_size: int | None


@dataclass(frozen=True)
class _ContainerTableRow:
    """One parsed containers table row with icon keys."""

    name: str
    status_icon: str
    health_icon: str
    uptime: str
    image: str


@dataclass(frozen=True)
class _ContainerRenderContext:
    """Container-specific table render inputs bundled for lint-friendly callsites."""

    draw: ImageDraw.ImageDraw
    inner: Rect
    label_h: int
    source_text: str
    font_size: int | str
    max_items: int | None
    color: str | None


@dataclass(frozen=True)
class _SynologyStatusRow:
    """One parsed Synology status row with an icon key."""

    name: str
    status_icon: str
    state: str


@dataclass(frozen=True)
class _SynologyStatusRenderContext:
    """Render context for Synology status icon table mode."""

    draw: ImageDraw.ImageDraw
    inner: Rect
    label_h: int
    source_text: str
    font_size: int | str
    max_items: int | None
    color: str | None


_ICON_RETRY_BACKOFF_SEC = 10.0
_ICON_CACHE: dict[str, Image.Image] = {}
_ICON_MTIME: dict[str, float] = {}
_ICON_RETRY_AFTER: dict[str, float] = {}
_STATUS_ICON_BASE = "assets/icons/status"
_HEALTH_ICON_BASE = "assets/icons/health"


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
        if cfg.source == "containers.rows":
            context = _ContainerRenderContext(
                draw=draw,
                inner=inner,
                label_h=label_h,
                source_text=source_text,
                font_size=cfg.font_size,
                max_items=cfg.max_items,
                color=cfg.color,
            )
            if self._draw_containers_table(img, context):
                return

        if cfg.source == "synology.status.rows":
            synology_context = _SynologyStatusRenderContext(
                draw=draw,
                inner=inner,
                label_h=label_h,
                source_text=source_text,
                font_size=cfg.font_size,
                max_items=cfg.max_items,
                color=cfg.color,
            )
            if self._draw_synology_status_table(img, synology_context):
                return

        rows = _parse_rows(source_text)

        if cfg.max_items is not None and cfg.max_items > 0:
            rows = rows[: cfg.max_items]

        if not rows:
            rows = [_Row(left="—", right="—")]

        avail_w = max(1, inner.w - 2)
        avail_h = max(1, inner.h - label_h)
        fit_text = bool(cfg.table_fit_text)
        expected_rows = max(1, cfg.max_items or len(rows))
        cache_key = (
            source_text,
            tuple((row.left, row.right) for row in rows),
            avail_w,
            avail_h,
            cfg.font_size,
            fit_text,
            expected_rows,
            cfg.max_font_size,
        )
        prepared = _layout_from_cache(_state, cache_key)
        if prepared is None:
            spec = _LayoutSpec(
                max_w=avail_w,
                max_h=avail_h,
                font_size=cfg.font_size,
                fit_text=fit_text,
                expected_rows=expected_rows,
                max_font_size=cfg.max_font_size,
            )
            prepared = _prepare_layout(draw, rows, spec)
            _state["table_layout_key"] = cache_key
            _state["table_layout"] = prepared

        color = parse_color(cfg.color, fallback=(220, 225, 230))
        # Table rows are anchored to the top of the content area.
        y = inner.y + label_h

        left_x = inner.x + 1
        right_x = inner.x + inner.w - 2

        muted_suffix_color = (75, 80, 88)
        for row in prepared.rows:
            left_bb = draw.textbbox((0, 0), row.left, font=prepared.font)
            left_y = y - int(left_bb[1])
            draw.text((left_x, left_y), row.left, fill=color, font=prepared.font)

            right_main, right_suffix = _split_phasing_suffix(row.right)
            right_bb = draw.textbbox((0, 0), row.right, font=prepared.font)
            right_y = y - int(right_bb[1])
            right_origin_x = right_x - row.right_width
            if right_suffix:
                main_w = _text_width(draw, prepared.font, right_main, None)
                draw.text(
                    (right_origin_x, right_y),
                    right_main,
                    fill=color,
                    font=prepared.font,
                )
                draw.text(
                    (right_origin_x + main_w, right_y),
                    right_suffix,
                    fill=muted_suffix_color,
                    font=prepared.font,
                )
            else:
                draw.text(
                    (right_origin_x, right_y),
                    row.right,
                    fill=color,
                    font=prepared.font,
                )
            y += prepared.row_h

    def _draw_containers_table(
        self,
        img: Image.Image,
        context: _ContainerRenderContext,
    ) -> bool:
        """Render containers.rows in 5-column mode with icon assets.

        Returns:
            True when container table mode was rendered.
        """
        rows = _parse_container_rows(context.source_text)
        if not rows:
            return False

        if context.max_items is not None and context.max_items > 0:
            rows = rows[: context.max_items]

        color = parse_color(context.color, fallback=(220, 225, 230))
        header_color = (160, 170, 182)
        inner = context.inner
        draw = context.draw
        y = inner.y + context.label_h + 2
        avail_w = inner.w - 4
        x0 = inner.x + 2
        name_x = x0
        status_x = x0 + int(avail_w * 0.31)
        health_x = x0 + int(avail_w * 0.43)
        uptime_x = x0 + int(avail_w * 0.56)
        image_x = x0 + int(avail_w * 0.75)

        resolved_font_size = _to_font_size(context.font_size, inner)
        header_font = get_font(max(10, int(resolved_font_size * 0.78)))
        body_font = get_font(resolved_font_size)

        draw.text((name_x, y), "Container", fill=header_color, font=header_font)
        draw.text((status_x, y), "Status", fill=header_color, font=header_font)
        draw.text((health_x, y), "Health", fill=header_color, font=header_font)
        draw.text((uptime_x, y), "Uptime", fill=header_color, font=header_font)
        draw.text((image_x, y), "Image", fill=header_color, font=header_font)

        header_bb = draw.textbbox((0, 0), "Ag", font=header_font)
        row_h = int((draw.textbbox((0, 0), "Ag", font=body_font)[3]) + 2)
        y += int(header_bb[3] - header_bb[1]) + 5
        draw.line((x0, y, inner.x + inner.w - 2, y), fill=(56, 64, 74), width=1)
        y += 4

        icon_size = max(10, row_h - 4)
        for row in rows:
            if y + row_h > inner.y + inner.h:
                break

            name_text = _ellipsize(
                draw,
                body_font,
                row.name,
                status_x - name_x - 6,
                {},
            )
            draw.text((name_x, y), name_text, fill=color, font=body_font)

            status_icon = _load_icon(f"{_STATUS_ICON_BASE}-{row.status_icon}.png")
            health_icon = _load_icon(f"{_HEALTH_ICON_BASE}-{row.health_icon}.png")
            if status_icon is not None:
                status_scaled = status_icon.resize(
                    (icon_size, icon_size),
                    Image.Resampling.LANCZOS,
                )
                img.paste(status_scaled, (status_x, y + 1), status_scaled)
            if health_icon is not None:
                health_scaled = health_icon.resize(
                    (icon_size, icon_size),
                    Image.Resampling.LANCZOS,
                )
                img.paste(health_scaled, (health_x, y + 1), health_scaled)

            draw.text((uptime_x, y), row.uptime, fill=color, font=body_font)
            draw.text(
                (image_x, y),
                _ellipsize(draw, body_font, row.image, inner.x + inner.w - image_x - 4, {}),
                fill=color,
                font=body_font,
            )
            y += row_h
        return True

    def _draw_synology_status_table(
        self,
        img: Image.Image,
        context: _SynologyStatusRenderContext,
    ) -> bool:
        """Render ``synology.status.rows`` as name + icon + state columns."""
        rows = _parse_synology_status_rows(context.source_text)
        if not rows:
            return False

        if context.max_items is not None and context.max_items > 0:
            rows = rows[: context.max_items]

        color = parse_color(context.color, fallback=(220, 225, 230))
        header_color = (160, 170, 182)
        muted_color = (134, 140, 148)
        draw = context.draw
        inner = context.inner
        y = inner.y + context.label_h + 2
        avail_w = inner.w - 4
        x0 = inner.x + 2
        name_x = x0
        icon_x = x0 + int(avail_w * 0.64)
        state_x = x0 + int(avail_w * 0.73)

        resolved_font_size = _to_font_size(context.font_size, inner)
        header_font = get_font(max(10, int(resolved_font_size * 0.78)))
        body_font = get_font(resolved_font_size)

        draw.text((name_x, y), "Service", fill=header_color, font=header_font)
        draw.text((icon_x, y), "", fill=header_color, font=header_font)
        draw.text((state_x, y), "State", fill=header_color, font=header_font)

        header_bb = draw.textbbox((0, 0), "Ag", font=header_font)
        row_h = int((draw.textbbox((0, 0), "Ag", font=body_font)[3]) + 2)
        y += int(header_bb[3] - header_bb[1]) + 5
        draw.line((x0, y, inner.x + inner.w - 2, y), fill=(56, 64, 74), width=1)
        y += 4

        icon_size = max(10, row_h - 4)
        for row in rows:
            if y + row_h > inner.y + inner.h:
                break

            name_text = _ellipsize(draw, body_font, row.name, icon_x - name_x - 6, {})
            draw.text((name_x, y), name_text, fill=color, font=body_font)

            icon = _load_icon(f"assets/icons/status-{row.status_icon}.png")
            if icon is not None:
                icon_scaled = icon.resize((icon_size, icon_size), Image.Resampling.LANCZOS)
                img.paste(icon_scaled, (icon_x, y + 1), icon_scaled)

            state_color = muted_color if row.status_icon == "unknown" else color
            draw.text((state_x, y), row.state, fill=state_color, font=body_font)
            y += row_h
        return True


def _layout_from_cache(
    state: dict[str, object],
    cache_key: tuple[object, ...],
) -> _PreparedLayout | None:
    """Return cached table layout when the current draw key matches."""
    cached_key = state.get("table_layout_key")
    cached_layout = state.get("table_layout")
    if cached_key != cache_key:
        return None
    if isinstance(cached_layout, _PreparedLayout):
        return cached_layout
    return None


def _prepare_layout(
    draw: ImageDraw.ImageDraw,
    rows: list[_Row],
    spec: _LayoutSpec,
) -> _PreparedLayout:
    """Measure and prepare row text for drawing."""
    font, row_h, left_w, _gap, keep_full_left = _fit_font(draw, rows, spec)
    width_cache: dict[str, int] = {}
    prepared_rows: list[_PreparedRow] = []
    for row in rows:
        left = row.left
        if not keep_full_left:
            left = _ellipsize(draw, font, left, left_w, width_cache)
        right_width = _text_width(draw, font, row.right, width_cache)
        prepared_rows.append(_PreparedRow(left=left, right=row.right, right_width=right_width))
    return _PreparedLayout(
        font=font,
        row_h=row_h,
        left_w=left_w,
        rows=tuple(prepared_rows),
    )


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


def _parse_container_rows(source_text: str) -> list[_ContainerTableRow]:
    """Parse 5-column containers table rows.

    Format per line: ``name|status_icon|health_icon|uptime|image``.
    """
    rows: list[_ContainerTableRow] = []
    for raw_line in source_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("|", maxsplit=4)
        if len(parts) != 5:
            return []
        rows.append(
            _ContainerTableRow(
                name=parts[0].strip() or "—",
                status_icon=parts[1].strip() or "unknown",
                health_icon=parts[2].strip() or "unknown",
                uptime=parts[3].strip() or "-",
                image=parts[4].strip() or "—",
            )
        )
    return rows


def _parse_synology_status_rows(source_text: str) -> list[_SynologyStatusRow]:
    """Parse 3-column Synology status rows: ``name|status_icon|state``."""
    rows: list[_SynologyStatusRow] = []
    for raw_line in source_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("|", maxsplit=2)
        if len(parts) != 3:
            return []
        rows.append(
            _SynologyStatusRow(
                name=parts[0].strip() or "—",
                status_icon=parts[1].strip() or "unknown",
                state=parts[2].strip() or "unknown",
            )
        )
    return rows


def _to_font_size(font_size: int | str, inner: Rect) -> int:
    """Resolve table font size value to a concrete integer."""
    if isinstance(font_size, int):
        return max(10, font_size)
    return max(12, min(inner.h // 12, inner.w // 34))


def _load_icon(path_text: str) -> Image.Image | None:
    """Load and cache icon image by path with local mtime invalidation."""
    retry_after = _ICON_RETRY_AFTER.get(path_text)
    if retry_after is not None and retry_after > time.monotonic():
        return None

    path = Path(path_text)
    if not path.is_file():
        _ICON_RETRY_AFTER[path_text] = time.monotonic() + _ICON_RETRY_BACKOFF_SEC
        return None

    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = -1.0

    cached = _ICON_CACHE.get(path_text)
    cached_mtime = _ICON_MTIME.get(path_text)
    if cached is not None and cached_mtime == mtime:
        return cached

    try:
        loaded = Image.open(path).convert("RGBA")
    except OSError:
        _ICON_RETRY_AFTER[path_text] = time.monotonic() + _ICON_RETRY_BACKOFF_SEC
        return None

    _ICON_CACHE[path_text] = loaded
    _ICON_MTIME[path_text] = mtime
    _ICON_RETRY_AFTER.pop(path_text, None)
    return loaded


def _strip_rank_prefix(value: str) -> str:
    """Remove leading numeric rank markers like ``1. `` from legacy rows."""
    return re.sub(r"^\s*\d+\.\s+", "", value)


def _split_phasing_suffix(value: str) -> tuple[str, str]:
    """Split right-column text into main value and muted phasing suffix."""
    suffix = " (phasing)"
    if value.endswith(suffix):
        return value[: -len(suffix)], suffix
    return value, ""


def _fit_font(
    draw: ImageDraw.ImageDraw,
    rows: list[_Row],
    spec: _LayoutSpec,
) -> tuple[FreeTypeFont | ImageFont, int, int, int, bool]:
    """Pick the largest font and row geometry that fit the table box."""
    row_count = max(1, len(rows))
    sizing_row_count = max(row_count, spec.expected_rows)
    max_w = spec.max_w
    max_h = spec.max_h
    font_size = spec.font_size
    dynamic_min = max(1, min(max_w, max_h) // 34)
    if font_size == "auto":
        # Keep auto text responsive but avoid one-row tables exploding to unreadable sizes.
        by_rows = max(1, max_h // sizing_row_count)
        by_width = max(1, max_w // 34)
        by_height = max(1, max_h // 12)
        dynamic_max = max(dynamic_min, min(by_width, by_height))
        start_size = max(dynamic_min, min(by_rows, dynamic_max))
    else:
        start_size = max(dynamic_min, int(font_size))

    capped_start = start_size
    if spec.max_font_size is not None:
        capped_start = min(start_size, spec.max_font_size)
    for size in range(capped_start, dynamic_min - 1, -1):
        font = get_font(size)
        row_h = _line_height(draw, font, size)
        total_h = row_h * row_count
        if total_h > max_h:
            continue

        right_w = _max_text_width(draw, font, [row.right for row in rows])
        gap = max(2, size // 3)
        full_left_w = _max_text_width(draw, font, [row.left for row in rows])
        if spec.fit_text:
            # Keep shrinking until both columns fit fully on one line
            if right_w + gap + full_left_w <= max_w:
                return font, row_h, full_left_w, gap, True
            continue  # try a smaller font size

        left_w = max_w - right_w - gap
        if left_w < max(8, max_w // 4):
            continue
        return font, row_h, left_w, gap, False

    fallback = get_font(dynamic_min)
    fallback_h = _line_height(draw, fallback, dynamic_min)
    fallback_gap = max(1, dynamic_min // 3)
    fallback_right = _max_text_width(draw, fallback, [row.right for row in rows])
    fallback_left = max(1, max_w - fallback_right - fallback_gap)
    # In fit_text mode use full left-column width — text overflows rather than truncates
    if spec.fit_text:
        full_left = _max_text_width(draw, fallback, [row.left for row in rows])
        return fallback, fallback_h, full_left, fallback_gap, True
    return fallback, fallback_h, fallback_left, fallback_gap, False


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
        width = max(width, _text_width(draw, font, value, None))
    return width


def _text_width(
    draw: ImageDraw.ImageDraw,
    font: FreeTypeFont | ImageFont,
    text: str,
    cache: dict[str, int] | None,
) -> int:
    """Return text width, optionally memoized for repeated measurements."""
    if cache is not None:
        cached = cache.get(text)
        if cached is not None:
            return cached
    bb = draw.textbbox((0, 0), text, font=font)
    width = int(bb[2] - bb[0])
    if cache is not None:
        cache[text] = width
    return width


def _ellipsize(
    draw: ImageDraw.ImageDraw,
    font: FreeTypeFont | ImageFont,
    text: str,
    max_w: int,
    width_cache: dict[str, int],
) -> str:
    """Trim text with ellipsis so it fits within max_w pixels."""
    if max_w <= 0:
        return ""

    if _text_width(draw, font, text, width_cache) <= max_w:
        return text

    ellipsis = "..."
    ell_w = _text_width(draw, font, ellipsis, width_cache)
    if ell_w >= max_w:
        return ""

    lo = 0
    hi = len(text)
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = f"{text[:mid]}{ellipsis}"
        if _text_width(draw, font, candidate, width_cache) <= max_w:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1
    if best:
        return best
    return ""
