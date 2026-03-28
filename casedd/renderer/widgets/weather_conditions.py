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


def _condition_kind(condition: str) -> str:
    """Map free-form condition text to a coarse icon kind."""
    text = condition.lower()
    checks: list[tuple[str, tuple[str, ...]]] = [
        ("storm", ("thunder", "storm")),
        ("snow", ("snow", "sleet")),
        ("rain", ("rain", "shower", "drizzle")),
        ("fog", ("fog", "haze", "mist")),
        ("partly", ("partly", "mostly")),
        ("cloud", ("cloud", "overcast")),
    ]
    for kind, tokens in checks:
        if any(token in text for token in tokens):
            return kind
    return "sun"


def _draw_condition_icon(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, kind: str) -> None:
    """Draw a small condition icon using primitive shapes only."""
    sun = (248, 210, 94)
    cloud = (196, 206, 216)
    rain = (108, 186, 242)
    storm = (255, 140, 86)

    if kind == "sun":
        draw.ellipse((x + 6, y + 6, x + size - 6, y + size - 6), fill=sun)
        return

    if kind == "partly":
        draw.ellipse((x + 3, y + 2, x + size - 14, y + size - 15), fill=sun)
        draw.ellipse((x + 10, y + 12, x + size - 2, y + size - 4), fill=cloud)
        draw.ellipse((x + 2, y + 15, x + size - 14, y + size - 4), fill=cloud)
        return

    draw.ellipse((x + 8, y + 10, x + size - 2, y + size - 4), fill=cloud)
    draw.ellipse((x + 1, y + 13, x + size - 13, y + size - 4), fill=cloud)
    draw.rectangle((x + 7, y + 17, x + size - 6, y + size - 3), fill=cloud)

    if kind == "rain":
        draw.line((x + 9, y + size - 2, x + 7, y + size + 5), fill=rain, width=2)
        draw.line((x + 16, y + size - 2, x + 14, y + size + 5), fill=rain, width=2)
        draw.line((x + 23, y + size - 2, x + 21, y + size + 5), fill=rain, width=2)
    elif kind == "storm":
        bolt = [
            (x + 16, y + 16),
            (x + 12, y + 25),
            (x + 18, y + 25),
            (x + 14, y + 34),
            (x + 23, y + 22),
            (x + 17, y + 22),
        ]
        draw.polygon(bolt, fill=storm)
    elif kind == "snow":
        draw.line((x + 10, y + 25, x + 22, y + 37), fill=(220, 232, 246), width=2)
        draw.line((x + 22, y + 25, x + 10, y + 37), fill=(220, 232, 246), width=2)
        draw.line((x + 16, y + 23, x + 16, y + 39), fill=(220, 232, 246), width=2)
    elif kind == "fog":
        fog = (170, 180, 192)
        draw.line((x + 3, y + 24, x + size - 3, y + 24), fill=fog, width=2)
        draw.line((x + 5, y + 29, x + size - 5, y + 29), fill=fog, width=2)
        draw.line((x + 3, y + 34, x + size - 3, y + 34), fill=fog, width=2)


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
        temp_f = _to_float(data.get(f"{root}.temp_f"))
        wind = _to_float(data.get(f"{root}.wind_mph"))
        humidity = _to_float(data.get(f"{root}.humidity_percent"))
        forecast_short = str(data.get(f"{root}.forecast_short") or "")

        temp_font = get_font(max(20, inner.h // 5))
        body_font = get_font(max(12, inner.h // 14))
        accent = parse_color(cfg.color, fallback=(102, 224, 140))

        y = inner.y + label_h + 4
        icon_size = max(28, inner.h // 6)
        _draw_condition_icon(draw, inner.x + 4, y + 1, icon_size, _condition_kind(conditions))
        draw.text((inner.x + icon_size + 10, y), f"{temp_f:.1f} F", fill=accent, font=temp_font)
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
        if forecast_short and y + 16 < inner.y + inner.h - 16:
            y += 16
            draw.text(
                (inner.x + 4, y),
                f"Next: {forecast_short[:54]}",
                fill=(164, 176, 188),
                font=body_font,
            )
