"""Weather alerts/watch-warning widget renderer."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from casedd.data_store import DataStore, StoreValue
from casedd.renderer.color import parse_color
from casedd.renderer.fonts import get_font
from casedd.renderer.widgets.base import BaseWidget, content_rect, draw_label, fill_background
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig


def _to_float(value: StoreValue | None, default: float = 0.0) -> float:
    """Convert a store value to float with fallback."""
    if value is None:
        return default
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(value)
    except ValueError:
        return default


class WeatherAlertsWidget(BaseWidget):
    """Render active weather watch/warning information."""

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        _state: dict[str, object],
    ) -> None:
        """Paint weather alerts widget."""
        fill_background(img, rect, cfg.background)
        inner = content_rect(rect, cfg.padding)
        draw = ImageDraw.Draw(img)

        prefix = cfg.source.strip() if cfg.source else "weather.alert_summary"
        if prefix.endswith(".alert_summary"):
            root = prefix[: -len(".alert_summary")]
        elif prefix.endswith("."):
            root = prefix[:-1]
        else:
            root = prefix

        title = cfg.label if cfg.label else "Watches / Warnings"
        label_h = draw_label(draw, inner, title, color=(150, 150, 150))

        count = int(_to_float(data.get(f"{root}.alert_count"), 0.0))
        summary = str(data.get(f"{root}.alert_summary") or "No active alerts")
        watch_warning = str(data.get(f"{root}.watch_warning") or summary)

        count_font = get_font(max(18, inner.h // 6))
        body_font = get_font(max(12, inner.h // 14))

        has_alerts = count > 0
        tone = parse_color(cfg.color, fallback=(228, 96, 96) if has_alerts else (102, 214, 140))
        count_text = f"{count} active" if has_alerts else "No active alerts"

        y = inner.y + label_h + 4
        draw.text((inner.x + 4, y), count_text, fill=tone, font=count_font)
        count_h = int(count_font.getbbox("Ag")[3] - count_font.getbbox("Ag")[1])
        y += count_h + 3

        wrapped = _wrap_lines(body_font, watch_warning, max_width=inner.w - 8)
        for line in wrapped[:4]:
            draw.text((inner.x + 4, y), line, fill=(220, 226, 232), font=body_font)
            y += 15

        if summary and summary != watch_warning and y + 14 < inner.y + inner.h:
            draw.text((inner.x + 4, y), summary[:80], fill=(172, 182, 192), font=body_font)


def _wrap_lines(
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    text: str,
    max_width: int,
) -> list[str]:
    """Wrap text by words for alert summary rendering."""
    words = text.split()
    if not words:
        return [""]

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip() if current else word
        bbox = font.getbbox(candidate)
        if bbox[2] - bbox[0] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines
