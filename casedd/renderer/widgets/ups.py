"""UPS status widget renderer.

Draws a compact single-panel UPS view with:
- battery bar with urgency color
- status line and key power metrics
- small rolling load sparkline (last 10 minutes)

The widget reads values from ``ups.*`` by default. Set ``source`` to a custom
prefix when external producers publish under a different namespace.
"""

from __future__ import annotations

from collections import deque
import time

from PIL import Image, ImageDraw

from casedd.data_store import DataStore, StoreValue
from casedd.renderer.color import parse_color
from casedd.renderer.fonts import fit_font, get_font
from casedd.renderer.widgets.base import BaseWidget, content_rect, fill_background
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig


def _to_float(value: StoreValue | None, default: float = 0.0) -> float:
    """Convert a store value to float with fallback.

    Args:
        value: Raw store value.
        default: Fallback when conversion fails.

    Returns:
        Parsed float value.
    """
    if value is None:
        return default
    if isinstance(value, float):
        return value
    if isinstance(value, int):
        return float(value)
    try:
        return float(value)
    except ValueError:
        return default


def _to_text(value: StoreValue | None, default: str = "") -> str:
    """Convert a store value to display string.

    Args:
        value: Raw store value.
        default: Fallback display string.

    Returns:
        String representation.
    """
    if value is None:
        return default
    return str(value)


def _to_bool(value: StoreValue | None, default: bool = False) -> bool:
    """Convert a store value to bool with fallback.

    Args:
        value: Raw store value.
        default: Fallback when conversion fails.

    Returns:
        Parsed boolean value.
    """
    result = default
    if value is None:
        return result
    if isinstance(value, bool):
        result = value
    elif isinstance(value, (int, float)):
        result = value != 0
    elif isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            result = True
        elif normalized in {"0", "false", "no", "off"}:
            result = False
    return result


def _battery_color(percent: float) -> tuple[int, int, int]:
    """Select urgency color for battery percentage.

    Args:
        percent: Battery percentage.

    Returns:
        RGB tuple.
    """
    if percent < 20.0:
        return (220, 78, 78)
    if percent <= 50.0:
        return (230, 189, 75)
    return (82, 193, 112)


def _fmt_runtime(minutes: float) -> str:
    """Format runtime minutes as a human-readable duration.

    Args:
        minutes: Runtime in minutes.

    Returns:
        Compact duration string.
    """
    total = max(0, int(minutes))
    hours, mins = divmod(total, 60)
    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m"


class UpsWidget(BaseWidget):
    """Render UPS health and power state in one widget area."""

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        state: dict[str, object],
    ) -> None:
        """Paint the UPS widget.

        Args:
            img: Canvas image.
            rect: Widget bounds.
            cfg: Widget configuration.
            data: Live data store.
            state: Per-widget mutable state.
        """
        fill_background(img, rect, cfg.background)
        inner = content_rect(rect, cfg.padding)
        draw = ImageDraw.Draw(img)

        prefix = cfg.source.strip() if cfg.source else "ups"
        if not prefix:
            prefix = "ups"
        prefix = prefix.rstrip(".")

        status = _to_text(data.get(f"{prefix}.status"), "UNKNOWN").upper()
        battery = max(0.0, min(100.0, _to_float(data.get(f"{prefix}.battery_percent"), 0.0)))
        load_pct = max(0.0, _to_float(data.get(f"{prefix}.load_percent"), 0.0))
        load_watts = max(0.0, _to_float(data.get(f"{prefix}.load_watts"), 0.0))
        runtime_min = max(0.0, _to_float(data.get(f"{prefix}.runtime_minutes"), 0.0))
        input_v = max(0.0, _to_float(data.get(f"{prefix}.input_voltage"), 0.0))
        input_hz = max(0.0, _to_float(data.get(f"{prefix}.input_frequency"), 0.0))
        default_on_battery = status in {"ONBATT", "ON_BATTERY", "ON-BATTERY", "BATTERY"}
        on_battery = _to_bool(data.get(f"{prefix}.on_battery"), default_on_battery)
        in_use = _to_bool(data.get(f"{prefix}.in_use"), load_watts > 0.0)
        last_change_ts = _to_float(data.get(f"{prefix}.last_change_ts"), 0.0)

        title = cfg.label if cfg.label else "UPS"
        title_color = parse_color(cfg.color, fallback=(230, 230, 230))
        meta_color = (180, 180, 180)

        title_font = get_font(max(12, inner.h // 10))
        body_font = get_font(max(11, inner.h // 14))
        tiny_font = get_font(max(9, inner.h // 18))

        draw.text((inner.x + 4, inner.y + 2), title, fill=title_color, font=title_font)

        battery_y = inner.y + max(18, inner.h // 8)
        battery_h = max(20, inner.h // 5)
        battery_w = max(50, inner.w - 40)
        battery_x = inner.x + 6
        self._draw_battery(draw, battery_x, battery_y, battery_w, battery_h, battery)

        status_text = f"{status} | {battery:.0f}%"
        draw.text(
            (battery_x + 2, battery_y + battery_h + 4),
            status_text,
            fill=meta_color,
            font=body_font,
        )

        row1_y = battery_y + battery_h + max(20, inner.h // 8)
        row2_y = row1_y + max(16, inner.h // 12)
        draw.text(
            (inner.x + 6, row1_y),
            f"Load: {load_pct:.0f}% ({load_watts:.0f} W)",
            fill=(212, 212, 212),
            font=body_font,
        )
        draw.text(
            (inner.x + 6, row2_y),
            f"Runtime: {_fmt_runtime(runtime_min)}",
            fill=(212, 212, 212),
            font=body_font,
        )

        row3_y = row2_y + max(16, inner.h // 12)
        draw.text(
            (inner.x + 6, row3_y),
            f"Input: {input_v:.0f} V  {input_hz:.1f} Hz",
            fill=(185, 185, 185),
            font=body_font,
        )

        row4_y = row3_y + max(16, inner.h // 12)
        mode = "battery" if on_battery else "line"
        use_state = "in use" if in_use else "idle"
        mode_color = (220, 120, 90) if on_battery else (112, 200, 140)
        draw.text(
            (inner.x + 6, row4_y),
            f"Mode: {mode} | UPS: {use_state}",
            fill=mode_color,
            font=body_font,
        )

        if last_change_ts > 0.0:
            ago_seconds = max(0, int(time.time() - last_change_ts))
            draw.text(
                (inner.x + 6, row4_y + max(14, inner.h // 13)),
                f"Last change: {ago_seconds}s ago",
                fill=(140, 140, 140),
                font=tiny_font,
            )

        self._draw_load_sparkline(draw, inner, state, load_pct)

    def _draw_battery(  # noqa: PLR0913 -- explicit geometry params keep helper stateless
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        w: int,
        h: int,
        percent: float,
    ) -> None:
        """Draw battery icon and fill level.

        Args:
            draw: PIL draw context.
            x: Left coordinate.
            y: Top coordinate.
            w: Width.
            h: Height.
            percent: Battery percentage.
        """
        outline = (190, 190, 190)
        cap_w = max(4, w // 28)
        body_w = w - cap_w - 3

        draw.rounded_rectangle([x, y, x + body_w, y + h], radius=4, outline=outline, width=2)
        cap_x0 = x + body_w + 2
        draw.rectangle(
            [cap_x0, y + h // 3, cap_x0 + cap_w, y + (2 * h) // 3],
            fill=outline,
        )

        fill_w = max(0, int((body_w - 4) * max(0.0, min(100.0, percent)) / 100.0))
        fill_color = _battery_color(percent)
        if fill_w > 0:
            draw.rounded_rectangle(
                [x + 2, y + 2, x + 2 + fill_w, y + h - 2],
                radius=3,
                fill=fill_color,
            )

    def _draw_load_sparkline(
        self,
        draw: ImageDraw.ImageDraw,
        inner: Rect,
        state: dict[str, object],
        load_pct: float,
    ) -> None:
        """Draw mini load-percent history sparkline.

        Args:
            draw: PIL draw context.
            inner: Inset content rect.
            state: Per-widget mutable state.
            load_pct: Current load percent value.
        """
        key = "ups_load_buf"
        now = time.monotonic()
        if key not in state:
            state[key] = deque[tuple[float, float]]()
        raw = state[key]
        if not isinstance(raw, deque):
            raw = deque[tuple[float, float]]()
            state[key] = raw
        buf: deque[tuple[float, float]] = raw

        buf.append((now, max(0.0, min(100.0, load_pct))))
        cutoff = now - 600.0
        while buf and buf[0][0] < cutoff:
            buf.popleft()
        while len(buf) > 200:
            buf.popleft()

        values = [sample for _, sample in buf]
        if len(values) < 2:
            return

        area_h = max(18, inner.h // 6)
        area_y = inner.y + inner.h - area_h - 4
        area_x = inner.x + 6
        area_w = inner.w - 12

        draw.rectangle([area_x, area_y, area_x + area_w, area_y + area_h], fill=(26, 26, 26))

        points: list[tuple[int, int]] = []
        denom = max(1, len(values) - 1)
        for idx, value in enumerate(values):
            x = area_x + int((idx * area_w) / denom)
            y = area_y + area_h - int((max(0.0, min(100.0, value)) / 100.0) * area_h)
            points.append((x, y))

        draw.line(points, fill=(110, 200, 245), width=2)

        current = f"{values[-1]:.0f}%"
        label_font = fit_font(current, max(20, area_w // 5), area_h)
        bbox = label_font.getbbox(current)
        tw = bbox[2] - bbox[0]
        draw.text(
            (area_x + area_w - tw - 2, area_y + 1),
            current,
            fill=(170, 220, 245),
            font=label_font,
        )
