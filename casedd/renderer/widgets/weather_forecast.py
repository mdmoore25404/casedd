"""Weather forecast table widget renderer."""

from __future__ import annotations

from PIL import Image, ImageDraw

from casedd.data_store import DataStore
from casedd.renderer.color import parse_color
from casedd.renderer.fonts import get_font
from casedd.renderer.widgets.base import BaseWidget, content_rect, draw_label, fill_background
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig


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
        rows = [line for line in table.splitlines() if line.strip()]
        if not rows:
            rows = ["DAY  LO/HI  PCP  WIND", "--   --/--  --   --"]

        body_font = get_font(max(11, inner.h // 12))
        accent = parse_color(cfg.color, fallback=(166, 218, 255))

        y = inner.y + label_h + 2
        for idx, line in enumerate(rows[:6]):
            color = accent if idx == 0 else (220, 226, 232)
            draw.text((inner.x + 4, y), line, fill=color, font=body_font)
            y += 15
