"""Boolean widget renderer.

Displays boolean state with a large icon for quick at-a-glance status:
- True/On: green circle with check mark
- False/Off: red circle with slash
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from casedd.data_store import DataStore
from casedd.renderer.color import parse_color
from casedd.renderer.widgets.base import BaseWidget, content_rect, draw_label, fill_background
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig


class BooleanWidget(BaseWidget):
    """Render a boolean value as a status icon.

    The widget reads from ``source`` (or ``content``), coerces to boolean,
    and paints a check/slash icon with high contrast for visibility.
    """

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        _state: dict[str, object],
    ) -> None:
        """Paint the boolean status widget onto ``img``.

        Args:
            img: Canvas image.
            rect: Widget bounding box.
            cfg: Widget configuration.
            data: Live data store.
            _state: Unused per-widget mutable state.
        """
        fill_background(img, rect, cfg.background)
        inner = content_rect(rect, cfg.padding)
        draw = ImageDraw.Draw(img)

        label_h = 0
        if cfg.label:
            label_h = draw_label(draw, inner, cfg.label, color=(150, 150, 150))

        raw_value = data.get(cfg.source) if cfg.source is not None else cfg.content
        is_true = _coerce_bool(raw_value)

        icon_rect = Rect(
            x=inner.x,
            y=inner.y + label_h,
            w=inner.w,
            h=max(1, inner.h - label_h),
        )
        self._draw_icon(draw, icon_rect, is_true, cfg)

    def _draw_icon(
        self,
        draw: ImageDraw.ImageDraw,
        rect: Rect,
        is_true: bool,
        cfg: WidgetConfig,
    ) -> None:
        """Draw a check or slash icon based on the boolean value."""
        size = max(10, min(rect.w, rect.h) - 6)
        cx = rect.x + rect.w // 2
        cy = rect.y + rect.h // 2
        radius = size // 2
        left = cx - radius
        top = cy - radius
        right = cx + radius
        bottom = cy + radius

        stroke = max(2, size // 14)
        true_color = parse_color(cfg.color, fallback=(109, 229, 143))
        false_color = (228, 85, 85)
        icon_color = true_color if is_true else false_color

        draw.ellipse(
            [(left, top), (right, bottom)],
            outline=icon_color,
            width=stroke,
        )

        if is_true:
            p1 = (cx - size // 4, cy + size // 30)
            p2 = (cx - size // 14, cy + size // 5)
            p3 = (cx + size // 4, cy - size // 6)
            draw.line([p1, p2], fill=icon_color, width=stroke)
            draw.line([p2, p3], fill=icon_color, width=stroke)
            return

        slash_pad = size // 4
        draw.line(
            [
                (left + slash_pad, bottom - slash_pad),
                (right - slash_pad, top + slash_pad),
            ],
            fill=icon_color,
            width=stroke,
        )


def _coerce_bool(value: object | None) -> bool:
    """Coerce common scalar values into a boolean state."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "on", "enabled", "yes", "y"}:
            return True
        if lowered in {"0", "false", "off", "disabled", "no", "n", ""}:
            return False
    return False


