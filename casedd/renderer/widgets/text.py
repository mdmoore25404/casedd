"""Text widget renderer.

Displays a string value or literal content, with word-wrap if the text
exceeds the widget width.

Example .casedd config:

.. code-block:: yaml

    hostname:
      type: text
      source: system.hostname
      label: "Host"
      font_size: 14
      color: "#aaaaaa"
"""

from __future__ import annotations

from typing import cast

from PIL import Image, ImageDraw, ImageFont

from casedd.data_store import DataStore
from casedd.renderer.color import parse_color
from casedd.renderer.fonts import fit_font, get_font
from casedd.renderer.widgets.base import (
    BaseWidget,
    content_rect,
    draw_label,
    fill_background,
    resolve_value,
)
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig

_STATUS_COLORS: dict[str, tuple[int, int, int]] = {
    "good": (46, 204, 113),
    "marginal": (242, 204, 61),
    "critical": (228, 85, 85),
    "out_of_spec": (228, 85, 85),
}


def _wrap_text(
    text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, max_width: int
) -> list[str]:
    """Wrap a string to fit within ``max_width`` pixels using the given font.

    Uses a greedy word-wrapping algorithm. Tokens that exceed width are
    split into width-fitting chunks so long domains/clients stay legible.

    Args:
        text: The full string to wrap.
        font: A PIL font object (FreeTypeFont or ImageFont).
        max_width: Maximum line width in pixels.

    Returns:
        List of wrapped line strings.
    """
    # Preserve explicit line breaks while still applying word-wrap per line.
    wrapped: list[str] = []
    for raw_line in text.splitlines() or [text]:
        words = raw_line.split()
        if not words:
            wrapped.append("")
            continue

        current = ""
        for word in words:
            token = word
            parts = _split_token_to_fit(token, font, max_width)
            if len(parts) > 1:
                if current:
                    wrapped.append(current)
                    current = ""
                wrapped.extend(parts[:-1])
                token = parts[-1]

            candidate = f"{current} {token}".strip() if current else token
            bbox = font.getbbox(candidate)
            if bbox[2] - bbox[0] <= max_width:
                current = candidate
            else:
                if current:
                    wrapped.append(current)
                current = token
        if current:
            wrapped.append(current)

    return wrapped if wrapped else [""]


def _split_token_to_fit(
    token: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    """Split one oversized token into width-fitting chunks.

    Args:
        token: Word-like token to split.
        font: Font used for width checks.
        max_width: Maximum line width in pixels.

    Returns:
        One or more chunks that each fit within ``max_width``.
    """
    if not token:
        return [""]
    if font.getbbox(token)[2] - font.getbbox(token)[0] <= max_width:
        return [token]

    chunks: list[str] = []
    current = ""
    for char in token:
        candidate = f"{current}{char}"
        bbox = font.getbbox(candidate)
        if bbox[2] - bbox[0] <= max_width:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = char
    if current:
        chunks.append(current)
    return chunks if chunks else [token]


def _line_height(font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> int:
    """Return a responsive line height for wrapped text blocks."""
    line_bbox = font.getbbox("Ag")
    base_h = int(line_bbox[3] - line_bbox[1])
    raw_size = getattr(font, "size", base_h)
    font_size = int(raw_size) if isinstance(raw_size, int | float) else base_h
    line_gap = max(1, font_size // 6)
    return int(base_h + line_gap)


class TextWidget(BaseWidget):
    """Renders a string value or static content with optional word-wrap."""

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        state: dict[str, object],
    ) -> None:
        """Paint the text widget onto ``img``.

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
        color = parse_color(cfg.color, fallback=(200, 200, 200))

        label_h = 0
        if cfg.label:
            label_h = draw_label(draw, inner, cfg.label, color=(150, 150, 150))

        raw = resolve_value(cfg, data)
        text = str(raw) if raw is not None else "--"

        available_w = inner.w
        available_h = max(1, inner.h - label_h)
        cache_key = (text, available_w, available_h, cfg.font_size)
        cached = self._fit_wrapped_font_from_cache(state, cache_key)
        if cached is None:
            font, lines = self._fit_wrapped_font(text, available_w, available_h, cfg.font_size)
            state["text_layout_key"] = cache_key
            state["text_layout_value"] = (font, tuple(lines))
        else:
            font, lines = cached

        if cfg.source == "speedtest.simple_summary" and self._draw_speedtest_simple(
            draw,
            inner,
            cfg,
            data,
            font,
            label_h,
        ):
            return

        # Calculate total text block height to vertically center it
        line_h = _line_height(font)
        total_h = line_h * len(lines)
        y_start = inner.y + label_h + max(0, (available_h - total_h) // 2)

        for i, line in enumerate(lines):
            bbox = font.getbbox(line)
            lw = bbox[2] - bbox[0]
            x = inner.x + (inner.w - lw) // 2 - bbox[0]
            y = y_start + i * line_h - bbox[1]
            draw.text((x, y), line, fill=color, font=font)

    def _fit_wrapped_font_from_cache(
        self,
        state: dict[str, object],
        cache_key: tuple[str, int, int, int | str],
    ) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, list[str]] | None:
        """Return cached wrapped text layout when inputs have not changed."""
        if state.get("text_layout_key") != cache_key:
            return None
        cached = state.get("text_layout_value")
        if not isinstance(cached, tuple) or len(cached) != 2:
            return None
        font_raw, lines_raw = cached
        if not hasattr(font_raw, "getbbox"):
            return None
        if not isinstance(lines_raw, tuple):
            return None
        lines: list[str] = []
        for entry in lines_raw:
            if not isinstance(entry, str):
                return None
            lines.append(entry)
        font = cast("ImageFont.FreeTypeFont | ImageFont.ImageFont", font_raw)
        return (font, lines)

    def _fit_wrapped_font(
        self,
        text: str,
        max_w: int,
        max_h: int,
        font_size: int | str,
    ) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, list[str]]:
        """Choose a responsive font size and wrapped lines for a text block."""
        dynamic_min = max(1, min(max_w, max_h) // 28)
        start_size = (
            max(1, max_h) if font_size == "auto" else max(dynamic_min, int(font_size))
        )

        for size in range(start_size, dynamic_min - 1, -1):
            candidate = get_font(size)
            lines = _wrap_text(text, candidate, max_w)
            line_h = _line_height(candidate)
            total_h = line_h * len(lines)
            widest_line = max(
                (candidate.getbbox(line)[2] - candidate.getbbox(line)[0]) for line in lines
            )
            if total_h <= max_h and widest_line <= max_w:
                return candidate, lines

        fallback = get_font(dynamic_min)
        return fallback, _wrap_text(text, fallback, max_w)

    def _draw_speedtest_simple(  # noqa: PLR0913 -- render helper needs explicit context
        self,
        draw: ImageDraw.ImageDraw,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        label_h: int,
    ) -> bool:
        """Draw speedtest summary as down/up segments with independent status colors.

        Uses ``getlength`` (not ``getbbox``) for inter-segment x advancement so
        that whitespace characters in the separator " / " are correctly accounted
        for.  Falls back to ``fit_font`` to shrink the font if the full text
        exceeds the available width at any font size.

        Args:
            draw: PIL draw context.
            rect: Widget bounding box.
            cfg: Widget config.
            data: Live data store.
            font: Font chosen for this widget.
            label_h: Vertical pixels consumed by the label.

        Returns:
            ``True`` when custom drawing was performed, else ``False``.
        """
        down_raw = data.get("speedtest.download_mbps")
        up_raw = data.get("speedtest.upload_mbps")
        if down_raw is None or up_raw is None:
            return False

        try:
            down = float(down_raw)
            up = float(up_raw)
        except (TypeError, ValueError):
            return False

        down_status_raw = data.get("speedtest.download_status")
        up_status_raw = data.get("speedtest.upload_status")
        down_status = str(down_status_raw).lower() if down_status_raw is not None else ""
        up_status = str(up_status_raw).lower() if up_status_raw is not None else ""

        down_text = f"{down:.0f}"
        mid_text = " / "
        up_text = f"{up:.0f} Mb/s"
        full_text = down_text + mid_text + up_text

        fallback_color = parse_color(cfg.color, fallback=(200, 200, 200))
        down_color = _STATUS_COLORS.get(down_status, fallback_color)
        up_color = _STATUS_COLORS.get(up_status, fallback_color)
        mid_color = parse_color(cfg.color, fallback=(185, 185, 185))

        def _advance(fnt: ImageFont.FreeTypeFont | ImageFont.ImageFont, s: str) -> int:
            """Return the full advance width of ``s``, including whitespace."""
            # getlength gives the true typographic advance (incl. leading/trailing
            # spaces) whereas getbbox trims invisible ink regions — critical for
            # the " / " separator whose leading space would be silently dropped.
            if isinstance(fnt, ImageFont.FreeTypeFont):
                return int(fnt.getlength(s))
            bb = fnt.getbbox(s)
            return int(bb[2] - bb[0])

        available_w = rect.w - 8
        available_h = rect.h - label_h

        # Shrink to the largest font where the full single-line text fits.
        current_font: ImageFont.FreeTypeFont | ImageFont.ImageFont = font
        if _advance(font, full_text) > available_w:
            current_font = fit_font(full_text, available_w, max(1, available_h - 4))

        total_advance = _advance(current_font, full_text)

        ref_bbox = current_font.getbbox("Ag")
        line_h = int(ref_bbox[3] - ref_bbox[1])
        # Subtract ref_bbox[1] to correct for the font origin offset so glyphs
        # visually centre within the available area (see anti-pattern blacklist).
        y = rect.y + label_h + max(0, (available_h - line_h) // 2) - int(ref_bbox[1])
        x = rect.x + max(0, (rect.w - total_advance) // 2)

        for seg_text, seg_color in [
            (down_text, down_color),
            (mid_text, mid_color),
            (up_text, up_color),
        ]:
            draw.text((x, y), seg_text, fill=seg_color, font=current_font)
            x += _advance(current_font, seg_text)

        return True
