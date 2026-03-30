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


@dataclass(frozen=True)
class _ForecastLayout:
    """Computed layout knobs for rendering forecast rows."""

    body_font: FreeTypeFont | ImageFont
    text_color: tuple[int, int, int]
    muted_color: tuple[int, int, int]
    y_start: int
    row_h: int
    icon_size: int
    icon_y_offset: int
    day_x: int
    right_lohi: int
    right_pcp: int
    right_wind: int
    show_wind: bool
    show_precip: bool
    tiny_mode: bool
    visible_rows: int


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

        layout = _compute_layout(cfg, inner, label_h, len(rows))
        _paint_rows(draw, inner, rows, layout)


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


def _compute_layout(
    cfg: WidgetConfig,
    inner: Rect,
    label_h: int,
    row_count: int,
) -> _ForecastLayout:
    """Compute responsive forecast layout settings for current widget rect."""
    requested = max(9, int(cfg.font_size)) if isinstance(cfg.font_size, int) else 9
    min_dim = max(1, min(inner.w, inner.h))
    auto_body = max(9, min(40, min_dim // 14, inner.w // 28, inner.h // 10))
    body_sz = max(9, min(max(requested, auto_body), inner.w // 20, inner.h // 6))
    body_font = get_font(body_sz)

    y_start = inner.y + label_h + 3
    text_h = int(body_font.getbbox("Ag")[3] - body_font.getbbox("Ag")[1])
    available_h = max(16, inner.h - label_h - 6)
    tiny_mode = inner.w < 210 or available_h < 54
    compact_mode = inner.w < 280 or available_h < 84
    max_rows = 2 if tiny_mode else (3 if compact_mode else 5)
    visible_rows = max(1, min(max_rows, row_count))
    row_h = max(text_h + 5, available_h // visible_rows)
    icon_size = max(10, min(row_h - 4, 36))

    return _ForecastLayout(
        body_font=body_font,
        text_color=(224, 230, 236),
        muted_color=(186, 196, 206),
        y_start=y_start,
        row_h=row_h,
        icon_size=icon_size,
        icon_y_offset=max(0, (row_h - icon_size) // 2),
        day_x=inner.x + (6 if tiny_mode else icon_size + 12),
        right_lohi=inner.x + int(inner.w * 0.48),
        right_pcp=inner.x + int(inner.w * 0.64),
        right_wind=inner.x + inner.w - 6,
        show_wind=(not compact_mode and inner.w >= 320),
        show_precip=(not tiny_mode and inner.w >= 250),
        tiny_mode=tiny_mode,
        visible_rows=visible_rows,
    )


def _paint_rows(
    draw: ImageDraw.ImageDraw,
    inner: Rect,
    rows: list[_ForecastRow],
    layout: _ForecastLayout,
) -> None:
    """Paint forecast rows using a precomputed layout plan."""
    y = layout.y_start
    for row in rows[: layout.visible_rows]:
        if y + layout.row_h > inner.y + inner.h:
            break
        if not layout.tiny_mode:
            _draw_tiny_icon(
                draw,
                inner.x + 4,
                y + layout.icon_y_offset,
                _condition_kind(row.condition),
                size=layout.icon_size,
            )
        draw.text(
            (layout.day_x, y),
            f"{row.day:>3}",
            fill=layout.text_color,
            font=layout.body_font,
        )

        lo_hi_text = f"{row.low:>2}/{row.high:>2}"
        rd = _RightDraw(draw=draw, font=layout.body_font, y=y)
        rd.emit(layout.right_lohi, lo_hi_text, layout.text_color)
        if layout.show_precip:
            rd.emit(layout.right_pcp, f"{row.precip:>3}", layout.muted_color)
        if layout.show_wind:
            rd.emit(layout.right_wind, row.wind[:11], layout.text_color)
        y += layout.row_h


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


def _draw_tiny_icon(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    kind: str,
    *,
    size: int,
) -> None:
    """Draw compact condition icon for forecast rows."""
    sun = (248, 210, 94)
    cloud = (188, 198, 208)
    rain = (102, 172, 232)
    s = max(12, size)
    y2 = y + s

    def sx(px: float) -> int:
        return x + int(px * s)

    def sy(py: float) -> int:
        return y + int(py * s)

    line_w = max(1, s // 10)

    if kind == "sun":
        draw.ellipse((sx(0.10), sy(0.10), sx(0.90), sy(0.90)), fill=sun)
        return

    if kind == "partly":
        draw.ellipse((sx(0.06), sy(0.06), sx(0.66), sy(0.66)), fill=sun)
        draw.ellipse((sx(0.38), sy(0.30), sx(0.98), sy(0.88)), fill=cloud)
        draw.ellipse((sx(0.06), sy(0.44), sx(0.72), sy(0.94)), fill=cloud)
        return

    draw.ellipse((sx(0.32), sy(0.32), sx(0.98), sy(0.88)), fill=cloud)
    draw.ellipse((sx(0.06), sy(0.44), sx(0.72), sy(0.94)), fill=cloud)
    if kind == "rain":
        draw.line((sx(0.38), sy(0.88), sx(0.25), y2), fill=rain, width=line_w)
        draw.line((sx(0.62), sy(0.88), sx(0.50), y2), fill=rain, width=line_w)
    elif kind == "storm":
        draw.line(
            (sx(0.50), sy(0.80), sx(0.38), sy(1.00)),
            fill=(255, 142, 94),
            width=max(2, line_w),
        )
        draw.line(
            (sx(0.38), sy(1.00), sx(0.62), sy(0.90)),
            fill=(255, 142, 94),
            width=max(2, line_w),
        )
    elif kind == "snow":
        draw.line((sx(0.50), sy(0.80), sx(0.50), y2), fill=(220, 232, 246), width=line_w)
        draw.line(
            (sx(0.38), sy(0.92), sx(0.62), sy(0.92)),
            fill=(220, 232, 246),
            width=line_w,
        )
    elif kind == "fog":
        draw.line(
            (sx(0.10), sy(0.82), sx(0.90), sy(0.82)),
            fill=(164, 176, 188),
            width=line_w,
        )
        draw.line(
            (sx(0.10), sy(1.00), sx(0.90), sy(1.00)),
            fill=(164, 176, 188),
            width=line_w,
        )
