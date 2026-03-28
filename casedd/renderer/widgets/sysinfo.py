"""System information widget renderer (neofetch-style).

Renders the key-value pairs produced by
:class:`~casedd.getters.sysinfo.SysinfoGetter` as a two-column table
reminiscent of the ``neofetch`` command-line tool.

Layout:
    - Left column: label in accent colour (e.g. teal-green)
    - Right column: value in off-white

Font size auto-scales to fill available height with all rows, capped to a
comfortable maximum so the widget looks good at any size.

Data source keys consumed:
    - ``{prefix}.rows`` (str) -- newline-delimited "Label|Value" pairs
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from casedd.data_store import DataStore
from casedd.renderer.color import parse_color
from casedd.renderer.fonts import get_font
from casedd.renderer.widgets.base import BaseWidget, content_rect, draw_label, fill_background
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig


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

        # Auto-scale font: fit all rows in available height, cap for readability
        num_rows = len(pairs)
        avail_h = inner.h - label_h - 8
        if isinstance(cfg.font_size, int):
            body_sz = cfg.font_size
        else:
            auto_sz = avail_h // max(1, num_rows + 1)
            body_sz = min(18, max(10, auto_sz))

        body_font = get_font(body_sz)
        sample_bb = draw.textbbox((0, 0), "Ag", font=body_font)
        row_h = int(sample_bb[3] - sample_bb[1]) + 4

        # Measure the widest key label to set the left column width
        label_widths = [
            int(draw.textbbox((0, 0), key, font=body_font)[2])
            for key, _ in pairs
        ]
        separator_w = int(draw.textbbox((0, 0), ":", font=body_font)[2])
        label_col_w = max(label_widths, default=60) + separator_w + 4

        left = inner.x + 4
        val_x = left + label_col_w
        right_limit = inner.x + inner.w - 4
        val_color: tuple[int, int, int] = (220, 225, 230)

        y = inner.y + label_h + 6
        for key, value in pairs:
            if y + row_h > inner.y + inner.h:
                break

            # Key label with trailing colon in accent colour
            draw.text((left, y), f"{key}:", fill=accent, font=body_font)

            # Value truncated to fit the remaining column width
            max_val_w = right_limit - val_x
            val = value
            while val:
                bbox = draw.textbbox((0, 0), val, font=body_font)
                if bbox[2] - bbox[0] <= max_val_w:
                    break
                val = val[:-1]
            draw.text((val_x, y), val, fill=val_color, font=body_font)
            y += row_h


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
