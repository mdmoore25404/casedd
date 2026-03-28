"""Weather current-conditions widget renderer."""

from __future__ import annotations

from PIL import Image, ImageDraw

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


class WeatherConditionsWidget(BaseWidget):
    """Render weather location and current condition metrics."""

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        _state: dict[str, object],
    ) -> None:
        """Paint weather conditions widget."""
        fill_background(img, rect, cfg.background)
        inner = content_rect(rect, cfg.padding)
        draw = ImageDraw.Draw(img)

        prefix = cfg.source.strip() if cfg.source else "weather.conditions"
        if prefix.endswith(".conditions"):
            root = prefix[: -len(".conditions")]
        elif prefix.endswith("."):
            root = prefix[:-1]
        else:
            root = prefix

        title = cfg.label if cfg.label else "Current Conditions"
        label_h = draw_label(draw, inner, title, color=(150, 150, 150))

        conditions = str(data.get(f"{root}.conditions") or "Unknown")
        location = str(data.get(f"{root}.location") or "Unknown location")
        provider = str(data.get(f"{root}.provider") or "")
        temp_f = _to_float(data.get(f"{root}.temp_f"))
        wind = _to_float(data.get(f"{root}.wind_mph"))
        humidity = _to_float(data.get(f"{root}.humidity_percent"))

        temp_font = get_font(max(20, inner.h // 5))
        body_font = get_font(max(12, inner.h // 14))
        accent = parse_color(cfg.color, fallback=(102, 224, 140))

        y = inner.y + label_h + 4
        draw.text((inner.x + 4, y), f"{temp_f:.1f} F", fill=accent, font=temp_font)
        temp_h = int(temp_font.getbbox("Ag")[3] - temp_font.getbbox("Ag")[1])
        y += temp_h + 2

        draw.text((inner.x + 4, y), conditions, fill=(225, 230, 235), font=body_font)
        y += 16
        draw.text(
            (inner.x + 4, y),
            f"Wind {wind:.1f} mph   Humidity {humidity:.0f}%",
            fill=(190, 198, 208),
            font=body_font,
        )
        y += 16
        draw.text((inner.x + 4, y), location, fill=(170, 178, 186), font=body_font)

        if provider:
            draw.text(
                (inner.x + inner.w - 140, inner.y + inner.h - 14),
                provider,
                fill=(120, 132, 142),
                font=get_font(max(10, inner.h // 20)),
            )
