"""Weather forecast table widget renderer."""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw
from PIL.ImageFont import FreeTypeFont, ImageFont

from casedd.data_store import DataStore, StoreValue
from casedd.renderer.fonts import get_font
from casedd.renderer.widgets.base import BaseWidget, content_rect, draw_label, fill_background
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig


@dataclass(frozen=True)
class _ForecastRow:
    """One rendered forecast table row."""

    day: str
    low: str
    high: str
    precip: str
    wind: str
    condition: str


class WeatherForecastWidget(BaseWidget):
    """Render compact daily forecast rows."""

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        _state: dict[str, object],
    ) -> None:
        """Paint forecast lines."""
        fill_background(img, rect, cfg.background)
        inner = content_rect(rect, cfg.padding)
        draw = ImageDraw.Draw(img)

        title = cfg.label if cfg.label else "Forecast"
        label_h = draw_label(draw, inner, title, color=(150, 150, 150))

        prefix = cfg.source.strip() if cfg.source else "weather.forecast_table"
        if prefix.endswith(".forecast_table"):
            root = prefix[: -len(".forecast_table")]
        elif prefix.endswith("."):
            root = prefix[:-1]
        else:
            root = prefix

        table = str(data.get(f"{root}.forecast_table") or "")
        rows = _parse_rows(table)
        if not rows:
            rows = [
                _ForecastRow("SAT", "30", "48", "0%", "9mph NW", "Partly cloudy"),
                _ForecastRow("SUN", "44", "62", "5%", "8mph S", "Cloudy"),
            ]

        if isinstance(cfg.font_size, int):
            body_sz = max(10, int(cfg.font_size))
        else:
            body_sz = max(11, min(36, inner.h // 12))
        body_font = get_font(body_sz)
        text_color = (224, 230, 236)
        muted_color = (186, 196, 206)

        y = inner.y + label_h + 3
        row_h = max(14, inner.h // 8)
        for row in rows[:5]:
            if y + row_h > inner.y + inner.h:
                break
            _draw_tiny_icon(draw, inner.x + 4, y + 1, _condition_kind(row.condition))
            draw.text((inner.x + 34, y), f"{row.day:>3}", fill=text_color, font=body_font)

            right_lohi = inner.x + min(inner.w - 132, 122)
            right_pcp = inner.x + min(inner.w - 78, 176)
            right_wind = inner.x + inner.w - 6
            lo_hi_text = f"{row.low:>2}/{row.high:>2}"
            rd = _RightDraw(draw=draw, font=body_font, y=y)
            rd.emit(right_lohi, lo_hi_text, text_color)
            rd.emit(right_pcp, f"{row.precip:>3}", muted_color)
            rd.emit(right_wind, row.wind[:11], text_color)
            y += row_h


@dataclass(frozen=True)
class _RightDraw:
    """Context for right-aligning one text cell."""

    draw: ImageDraw.ImageDraw
    font: FreeTypeFont | ImageFont
    y: int

    def emit(self, right_x: int, text: str, fill: tuple[int, int, int]) -> None:
        """Draw text right-aligned to right_x."""
        bbox = self.draw.textbbox((0, 0), text, font=self.font)
        width = bbox[2] - bbox[0]
        self.draw.text((right_x - width, self.y), text, fill=fill, font=self.font)


def _parse_rows(table: str) -> list[_ForecastRow]:
    """Parse forecast rows from pipe-delimited payload lines.

    Line format: DAY|LOW|HIGH|PRECIP|WIND|CONDITION
    """
    rows: list[_ForecastRow] = []
    for line in table.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        rows.append(_parse_row_line(stripped))
    return rows


def _parse_row_line(line: str) -> _ForecastRow:
    """Parse one forecast row line."""
    parts = [part.strip() for part in line.split("|", maxsplit=5)]
    if len(parts) < 6:
        return _ForecastRow("DAY", "--", "--", "--%", "--", "Cloudy")
    return _ForecastRow(
        day=parts[0].upper()[:3],
        low=_fmt_temp(_to_float(parts[1])),
        high=_fmt_temp(_to_float(parts[2])),
        precip=_fmt_percent(_to_float(parts[3])),
        wind=parts[4],
        condition=parts[5],
    )


def _fmt_temp(value: float | None) -> str:
    """Format one temperature cell."""
    if value is None:
        return "--"
    return f"{round(value):>2}"


def _fmt_percent(value: float | None) -> str:
    """Format one precipitation chance cell."""
    if value is None:
        return "--%"
    return f"{round(value):>2}%"


def _to_float(raw: StoreValue | str) -> float | None:
    """Convert scalar value to float."""
    if isinstance(raw, int | float):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            return None
    return None


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


def _draw_tiny_icon(draw: ImageDraw.ImageDraw, x: int, y: int, kind: str) -> None:
    """Draw compact condition icon for forecast rows."""
    sun = (248, 210, 94)
    cloud = (188, 198, 208)
    rain = (102, 172, 232)

    if kind == "sun":
        draw.ellipse((x + 2, y + 2, x + 13, y + 13), fill=sun)
        return

    if kind == "partly":
        draw.ellipse((x + 1, y + 1, x + 10, y + 10), fill=sun)
        draw.ellipse((x + 6, y + 5, x + 15, y + 13), fill=cloud)
        draw.ellipse((x + 1, y + 7, x + 11, y + 14), fill=cloud)
        return

    draw.ellipse((x + 5, y + 5, x + 15, y + 13), fill=cloud)
    draw.ellipse((x + 1, y + 7, x + 11, y + 14), fill=cloud)
    if kind == "rain":
        draw.line((x + 6, y + 13, x + 4, y + 16), fill=rain, width=1)
        draw.line((x + 10, y + 13, x + 8, y + 16), fill=rain, width=1)
    elif kind == "storm":
        draw.line((x + 8, y + 12, x + 6, y + 16), fill=(255, 142, 94), width=2)
        draw.line((x + 6, y + 16, x + 10, y + 14), fill=(255, 142, 94), width=2)
    elif kind == "snow":
        draw.line((x + 8, y + 12, x + 8, y + 17), fill=(220, 232, 246), width=1)
        draw.line((x + 6, y + 15, x + 10, y + 15), fill=(220, 232, 246), width=1)
    elif kind == "fog":
        draw.line((x + 2, y + 12, x + 14, y + 12), fill=(164, 176, 188), width=1)
        draw.line((x + 2, y + 15, x + 14, y + 15), fill=(164, 176, 188), width=1)
