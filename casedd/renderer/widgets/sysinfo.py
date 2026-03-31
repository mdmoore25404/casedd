"""System information widget renderer (neofetch-style).

Renders a compact dashboard inspired by ``neofetch`` / ``fastfetch``:

- Left column: ASCII-style distro logo accent.
- Right column: host header and key/value fact table.
- Footer: simple terminal-color palette blocks.

Data source keys consumed:
    - ``{prefix}.rows`` (str) -- newline-delimited "Label|Value" pairs.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from casedd.data_store import DataStore
from casedd.renderer.color import parse_color
from casedd.renderer.fonts import get_font
from casedd.renderer.widgets.base import BaseWidget, content_rect, draw_label, fill_background
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig

_FontT = ImageFont.FreeTypeFont | ImageFont.ImageFont


@dataclass(frozen=True)
class _LogoLayout:
    """Drawing parameters for the left logo block."""

    color: tuple[int, int, int]
    logo_x: int
    top_y: int
    content_h: int


@dataclass(frozen=True)
class _HeaderLayout:
    """Drawing parameters for the right-column header."""

    host_color: tuple[int, int, int]
    rule_color: tuple[int, int, int]
    right_x: int
    top_y: int
    right_w: int


@dataclass(frozen=True)
class _RowsLayout:
    """Drawing parameters for key/value row rendering."""

    accent: tuple[int, int, int]
    val_color: tuple[int, int, int]
    right_x: int
    right_w: int
    start_y: int
    row_h: int
    max_y: int


class SysinfoWidget(BaseWidget):
    """Render neofetch-style system info as a two-column key-value table."""

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        _state: dict[str, object],
    ) -> None:
        """Paint system information rows onto ``img`` within ``rect``.

        Args:
            img: Canvas image, modified in-place.
            rect: Allocated bounding box for this widget.
            cfg: Widget configuration (source, color, font_size, …).
            data: Live data store.
            _state: Per-widget state dict (unused).
        """
        fill_background(img, rect, cfg.background)
        inner = content_rect(rect, cfg.padding)
        draw = ImageDraw.Draw(img)

        title = cfg.label if cfg.label else "System Info"
        label_h = draw_label(draw, inner, title, color=(150, 150, 150))

        source = cfg.source.strip() if cfg.source else "sysinfo"
        prefix = source.removesuffix(".rows").rstrip(".")

        rows_raw = data.get(f"{prefix}.rows")
        pairs = _parse_pairs(str(rows_raw) if rows_raw is not None else "")

        if not pairs:
            pairs = [("Hostname", "…"), ("OS", "…"), ("Uptime", "…")]

        accent = parse_color(cfg.color, fallback=(96, 210, 165))
        val_color: tuple[int, int, int] = (220, 225, 230)
        host_color: tuple[int, int, int] = (240, 94, 106)
        rule_color: tuple[int, int, int] = (145, 155, 168)

        logo_col_w = max(140, int(inner.w * 0.34))
        top_y = inner.y + label_h + 6
        content_h = inner.h - label_h - 10
        right_x = inner.x + logo_col_w + 10
        right_w = inner.x + inner.w - 6 - right_x
        if right_w < 100:
            return

        body_font, row_h = _fit_body_font(draw, pairs, cfg.font_size, right_w, content_h)
        head_font = get_font(max(14, int(getattr(body_font, "size", 14) * 1.3)))
        logo_font = get_font(max(10, int(getattr(body_font, "size", 12) * 0.95)))

        _draw_logo(
            draw,
            pairs,
            logo_font,
            _LogoLayout(
                color=host_color,
                logo_x=inner.x + 8,
                top_y=top_y,
                content_h=content_h,
            ),
        )

        hostname = _pair_value(pairs, "Hostname")
        if hostname == "":
            hostname = "system"
        rule_y = _draw_header(
            draw,
            hostname,
            head_font,
            _HeaderLayout(
                host_color=host_color,
                rule_color=rule_color,
                right_x=right_x,
                top_y=top_y,
                right_w=right_w,
            ),
        )

        _draw_rows(
            draw,
            pairs,
            body_font,
            _RowsLayout(
                accent=accent,
                val_color=val_color,
                right_x=right_x,
                right_w=right_w,
                start_y=rule_y + 8,
                row_h=row_h,
                max_y=inner.y + inner.h - 20,
            ),
        )

        _draw_palette(draw, right_x, inner.y + inner.h - 14, right_w)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _parse_pairs(text: str) -> list[tuple[str, str]]:
    """Parse pipe-delimited key|value rows from the getter payload.

    Args:
        text: Multi-line string with one ``"Label|Value"`` per line.

    Returns:
        List of ``(label, value)`` tuples in order.
    """
    result: list[tuple[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split("|", maxsplit=1)
        if len(parts) == 2:
            result.append((parts[0], parts[1]))
    return result


def _pair_value(pairs: list[tuple[str, str]], key: str) -> str:
    """Return the value for ``key`` from key/value pair rows.

    Args:
        pairs: Parsed ``(key, value)`` rows.
        key: Key name to lookup.

    Returns:
        Value string when found, otherwise empty string.
    """
    for row_key, value in pairs:
        if row_key == key:
            return value
    return ""


def _fit_body_font(
    draw: ImageDraw.ImageDraw,
    pairs: list[tuple[str, str]],
    font_size: int | str,
    right_w: int,
    content_h: int,
) -> tuple[_FontT, int]:
    """Pick a body font size that fits row count and available width.

    Args:
        draw: PIL draw context.
        pairs: Key/value rows to render.
        font_size: Widget font_size setting.
        right_w: Width available for text table.
        content_h: Vertical space available for content.

    Returns:
        Chosen body font and per-row pixel height.
    """
    row_count = max(1, len(pairs))
    if isinstance(font_size, int):
        start = max(10, font_size)
    else:
        auto_by_height = content_h // max(1, row_count + 3)
        auto_by_width = right_w // 34
        start = max(11, min(40, auto_by_height, auto_by_width))

    for size in range(start, 9, -1):
        body_font = get_font(size)
        bb = draw.textbbox((0, 0), "Ag", font=body_font)
        row_h = int(bb[3] - bb[1]) + 4
        if row_h * row_count <= content_h - 26:
            return body_font, row_h

    fallback = get_font(10)
    fb = draw.textbbox((0, 0), "Ag", font=fallback)
    return fallback, int(fb[3] - fb[1]) + 3


def _truncate_to_width(
    draw: ImageDraw.ImageDraw,
    value: str,
    font: _FontT,
    max_width: int,
) -> str:
    """Truncate text to fit pixel width, appending an ellipsis when needed.

    Args:
        draw: PIL draw context.
        value: Raw text value.
        font: Active text font.
        max_width: Maximum allowed text width in pixels.

    Returns:
        Text that fits ``max_width``.
    """
    if max_width <= 0:
        return ""
    if int(draw.textbbox((0, 0), value, font=font)[2]) <= max_width:
        return value
    ellipsis = "..."
    trimmed = value
    while trimmed:
        candidate = f"{trimmed}{ellipsis}"
        if int(draw.textbbox((0, 0), candidate, font=font)[2]) <= max_width:
            return candidate
        trimmed = trimmed[:-1]
    return ellipsis


def _draw_logo(
    draw: ImageDraw.ImageDraw,
    pairs: list[tuple[str, str]],
    logo_font: _FontT,
    layout: _LogoLayout,
) -> None:
    """Draw a distro logo block on the left side."""
    logo_lines = _logo_lines_for_pairs(pairs)
    logo_sample = draw.textbbox((0, 0), "#", font=logo_font)
    logo_h = int(logo_sample[3] - logo_sample[1]) + 2
    logo_block_h = len(logo_lines) * logo_h
    logo_y = layout.top_y + max(0, (layout.content_h - logo_block_h) // 3)
    for i, line in enumerate(logo_lines):
        draw.text(
            (layout.logo_x, logo_y + i * logo_h),
            line,
            fill=layout.color,
            font=logo_font,
        )


def _draw_header(
    draw: ImageDraw.ImageDraw,
    hostname: str,
    head_font: _FontT,
    layout: _HeaderLayout,
) -> int:
    """Draw hostname header and return the y-coordinate of the separator line."""
    draw.text((layout.right_x, layout.top_y), hostname, fill=layout.host_color, font=head_font)
    head_bb = draw.textbbox((0, 0), hostname, font=head_font)
    head_h = int(head_bb[3] - head_bb[1])
    rule_y = layout.top_y + head_h + 4
    draw.line(
        (layout.right_x, rule_y, layout.right_x + layout.right_w, rule_y),
        fill=layout.rule_color,
        width=1,
    )
    return rule_y


def _draw_rows(
    draw: ImageDraw.ImageDraw,
    pairs: list[tuple[str, str]],
    body_font: _FontT,
    layout: _RowsLayout,
) -> None:
    """Draw key/value rows in the right column with value truncation."""
    label_widths = [
        int(draw.textbbox((0, 0), f"{key}:", font=body_font)[2])
        for key, _ in pairs
    ]
    label_col_w = min(max(label_widths, default=80) + 4, int(layout.right_w * 0.42))

    y = layout.start_y
    right_limit = layout.right_x + layout.right_w
    for key, value in pairs:
        if y + layout.row_h > layout.max_y:
            break
        draw.text((layout.right_x, y), f"{key}:", fill=layout.accent, font=body_font)
        val_x = layout.right_x + label_col_w
        val = _truncate_to_width(draw, value, body_font, right_limit - val_x)
        draw.text((val_x, y), val, fill=layout.val_color, font=body_font)
        y += layout.row_h


def _logo_lines_for_pairs(pairs: list[tuple[str, str]]) -> list[str]:
    """Return ASCII-style logo lines for the detected distro.

    Args:
        pairs: Key/value rows that include OS name.

    Returns:
        List of logo text lines.
    """
    os_name = _pair_value(pairs, "OS").lower()
    if "ubuntu" in os_name:
        return [
            "      .-::::-.",
            "   .:#########:.",
            "  :####:.  .:####:",
            " .###:        :###.",
            " .###:        :###.",
            "  :####:.  .:####:",
            "   ':#########:'",
            "      '-::::-'",
        ]
    return [
        "    .-======-.",
        "  .'  .--.   '.",
        " /   (____)    \\",
        "|    .----.     |",
        "|   (______)    |",
        " \\            ./",
        "  '.        .-'",
        "    '-.__.-'",
    ]


def _draw_palette(draw: ImageDraw.ImageDraw, x: int, y: int, width: int) -> None:
    """Draw a small 8-color terminal palette strip.

    Args:
        draw: PIL draw context.
        x: Left coordinate.
        y: Top coordinate.
        width: Available row width.
    """
    colors: list[tuple[int, int, int]] = [
        (39, 44, 52),
        (220, 80, 80),
        (100, 214, 122),
        (234, 196, 68),
        (90, 150, 245),
        (183, 108, 232),
        (74, 196, 214),
        (214, 214, 214),
    ]
    gap = 2
    cell_w = max(10, min(22, (width - gap * (len(colors) - 1)) // len(colors)))
    cell_h = 9
    for i, color in enumerate(colors):
        left = x + i * (cell_w + gap)
        draw.rectangle((left, y, left + cell_w, y + cell_h), fill=color)
