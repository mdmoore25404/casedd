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

from PIL import Image, ImageDraw

from casedd.data_store import DataStore
from casedd.renderer.color import parse_color
from casedd.renderer.fonts import get_font
from casedd.renderer.widgets.base import (
    BaseWidget,
    draw_label,
    fill_background,
    resolve_value,
)
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig


def _wrap_text(text: str, font: object, max_width: int) -> list[str]:
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
    # PIL font objects have getbbox; use it for accurate measurement
    words = text.split()
    lines: list[str] = []
    current = ""

    for word in words:
        candidate = f"{current} {word}".strip() if current else word
        bbox = font.getbbox(candidate)  # type: ignore[union-attr]
        if bbox[2] - bbox[0] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines if lines else [""]


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

        size = cfg.font_size if isinstance(cfg.font_size, int) else 14
        font = get_font(size)

        available_w = rect.w - 8
        lines = _wrap_text(text, font, available_w)

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
