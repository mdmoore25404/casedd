"""Weather current-conditions widget renderer."""

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

    s = max(16, size)
    y2 = y + s

    def sx(px: float) -> int:
        return x + int(px * s)

    def sy(py: float) -> int:
        return y + int(py * s)

    line_w = max(1, s // 10)

    if kind == "sun":
        draw.ellipse((sx(0.14), sy(0.14), sx(0.86), sy(0.86)), fill=sun)
        return

    if kind == "partly":
        draw.ellipse((sx(0.08), sy(0.06), sx(0.66), sy(0.66)), fill=sun)
        draw.ellipse((sx(0.36), sy(0.34), sx(0.96), sy(0.90)), fill=cloud)
        draw.ellipse((sx(0.06), sy(0.46), sx(0.72), sy(0.96)), fill=cloud)
        return

    draw.ellipse((sx(0.32), sy(0.30), sx(0.98), sy(0.88)), fill=cloud)
    draw.ellipse((sx(0.04), sy(0.44), sx(0.72), sy(0.96)), fill=cloud)
    draw.rectangle((sx(0.28), sy(0.52), sx(0.86), sy(0.94)), fill=cloud)

    if kind == "rain":
        draw.line((sx(0.36), sy(0.88), sx(0.26), y2), fill=rain, width=line_w)
        draw.line((sx(0.56), sy(0.88), sx(0.46), y2), fill=rain, width=line_w)
        draw.line((sx(0.76), sy(0.88), sx(0.66), y2), fill=rain, width=line_w)
    elif kind == "storm":
        bolt = [
            (sx(0.54), sy(0.52)),
            (sx(0.42), sy(0.74)),
            (sx(0.58), sy(0.74)),
            (sx(0.46), sy(0.98)),
            (sx(0.72), sy(0.68)),
            (sx(0.56), sy(0.68)),
        ]
        draw.polygon(bolt, fill=storm)
    elif kind == "snow":
        draw.line((sx(0.34), sy(0.72), sx(0.68), sy(1.00)), fill=(220, 232, 246), width=line_w)
        draw.line((sx(0.68), sy(0.72), sx(0.34), sy(1.00)), fill=(220, 232, 246), width=line_w)
        draw.line((sx(0.51), sy(0.66), sx(0.51), sy(1.00)), fill=(220, 232, 246), width=line_w)
    elif kind == "fog":
        fog = (170, 180, 192)
        draw.line((sx(0.10), sy(0.66), sx(0.90), sy(0.66)), fill=fog, width=line_w)
        draw.line((sx(0.14), sy(0.80), sx(0.86), sy(0.80)), fill=fog, width=line_w)
        draw.line((sx(0.10), sy(0.94), sx(0.90), sy(0.94)), fill=fog, width=line_w)


def _fit_text(font_size: int, inner: Rect, label_h: int, preferred_lines: int) -> int:
    """Clamp body text size to available space for a target line budget."""
    min_dim = max(1, min(inner.w, inner.h))
    auto_guess = max(9, min(46, min_dim // 12))
    target = max(font_size, auto_guess)
    available_h = max(24, inner.h - label_h - 8)
    max_by_height = max(9, available_h // max(2, preferred_lines))
    max_by_width = max(9, inner.w // 22)
    return max(9, min(target, max_by_height, max_by_width))


def _truncate_to_width(
    text: str,
    max_width: int,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> str:
    """Trim text with ellipsis so it fits in max_width pixels."""
    bbox = font.getbbox(text)
    if bbox[2] - bbox[0] <= max_width:
        return text

    candidate = text
    while len(candidate) > 1:
        candidate = candidate[:-1]
        trial = f"{candidate}..."
        trial_box = font.getbbox(trial)
        if trial_box[2] - trial_box[0] <= max_width:
            return trial
    return text


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

        requested = max(9, int(cfg.font_size)) if isinstance(cfg.font_size, int) else 9
        body_size = _fit_text(requested, inner, label_h, preferred_lines=7)
        temp_size = max(12, min(120, int(body_size * 2.05), inner.w // 3, inner.h // 3))
        temp_font = get_font(temp_size)
        body_font = get_font(body_size)
        accent = parse_color(cfg.color, fallback=(102, 224, 140))
        body_line_h = int(body_font.getbbox("Ag")[3] - body_font.getbbox("Ag")[1]) + max(
            2,
            body_size // 4,
        )

        y = inner.y + label_h + 4
        icon_size = max(16, min(inner.h // 3, int(body_size * 2.1), inner.w // 5))
        _draw_condition_icon(draw, inner.x + 4, y + 1, icon_size, _condition_kind(conditions))
        temp_x = inner.x + icon_size + max(6, body_size // 2)
        draw.text((temp_x, y), f"{temp_f:.1f} F", fill=accent, font=temp_font)
        temp_h = int(temp_font.getbbox("Ag")[3] - temp_font.getbbox("Ag")[1])
        y += temp_h + 2

        max_text_w = max(20, inner.w - 8)
        draw.text(
            (inner.x + 4, y),
            _truncate_to_width(conditions, max_text_w, body_font),
            fill=(225, 230, 235),
            font=body_font,
        )
        y += body_line_h
        if inner.w < 310:
            draw.text(
                (inner.x + 4, y),
                _truncate_to_width(f"Wind {wind:.1f} mph", max_text_w, body_font),
                fill=(190, 198, 208),
                font=body_font,
            )
            y += body_line_h
            if y + body_line_h <= inner.y + inner.h:
                draw.text(
                    (inner.x + 4, y),
                    _truncate_to_width(f"Humidity {humidity:.0f}%", max_text_w, body_font),
                    fill=(190, 198, 208),
                    font=body_font,
                )
        else:
            draw.text(
                (inner.x + 4, y),
                _truncate_to_width(
                    f"Wind {wind:.1f} mph   Humidity {humidity:.0f}%",
                    max_text_w,
                    body_font,
                ),
                fill=(190, 198, 208),
                font=body_font,
            )
        y += body_line_h
        draw.text(
            (inner.x + 4, y),
            _truncate_to_width(location, max_text_w, body_font),
            fill=(170, 178, 186),
            font=body_font,
        )
        if forecast_short and y + body_line_h < inner.y + inner.h - body_line_h:
            y += body_line_h
            draw.text(
                (inner.x + 4, y),
                _truncate_to_width(f"Next: {forecast_short}", max_text_w, body_font),
                fill=(164, 176, 188),
                font=body_font,
            )
