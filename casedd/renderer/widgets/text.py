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

from PIL import Image, ImageDraw, ImageFont

from casedd.data_store import DataStore
from casedd.renderer.color import parse_color
from casedd.renderer.fonts import fit_font, get_font
from casedd.renderer.widgets.base import (
    BaseWidget,
    choose_font_for_box,
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

    Uses a greedy word-wrapping algorithm. Long words that exceed the width
    on their own are not broken and will overflow.

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
            candidate = f"{current} {word}".strip() if current else word
            bbox = font.getbbox(candidate)
            if bbox[2] - bbox[0] <= max_width:
                current = candidate
            else:
                if current:
                    wrapped.append(current)
                current = word
        if current:
            wrapped.append(current)

    return wrapped if wrapped else [""]


class TextWidget(BaseWidget):
    """Renders a string value or static content with optional word-wrap."""

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        _state: dict[str, object],
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
        draw = ImageDraw.Draw(img)
        color = parse_color(cfg.color, fallback=(200, 200, 200))

        label_h = 0
        if cfg.label:
            label_h = draw_label(draw, rect, cfg.label, color=(150, 150, 150))

        raw = resolve_value(cfg, data)
        text = str(raw) if raw is not None else "--"

        available_w = rect.w - 8
        available_h = rect.h - label_h
        font, lines = self._fit_wrapped_font(text, available_w, available_h, cfg.font_size)

        if cfg.source == "speedtest.simple_summary" and self._draw_speedtest_simple(
            draw,
            rect,
            cfg,
            data,
            font,
            label_h,
        ):
            return

        # Calculate total text block height to vertically center it
        line_bbox = font.getbbox("Ag")
        line_h = line_bbox[3] - line_bbox[1] + 2
        total_h = line_h * len(lines)
        available_h = rect.h - label_h
        y_start = rect.y + label_h + max(0, (available_h - total_h) // 2)

        for i, line in enumerate(lines):
            bbox = font.getbbox(line)
            lw = bbox[2] - bbox[0]
            x = rect.x + (rect.w - lw) // 2
            draw.text((x, y_start + i * line_h), line, fill=color, font=font)

    def _fit_wrapped_font(
        self,
        text: str,
        max_w: int,
        max_h: int,
        font_size: int | str,
    ) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, list[str]]:
        """Choose a responsive font size and wrapped lines for a text block."""
        seed_font = choose_font_for_box(text or "--", max_w, max_h, font_size, min_size=10)
        start_size = getattr(seed_font, "size", 10)
        min_size = 8

        for size in range(start_size, min_size - 1, -1):
            candidate = get_font(size)
            lines = _wrap_text(text, candidate, max_w)
            line_bbox = candidate.getbbox("Ag")
            line_h = line_bbox[3] - line_bbox[1] + 2
            total_h = line_h * len(lines)
            if total_h <= max_h:
                return candidate, lines

        fallback = get_font(min_size)
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
