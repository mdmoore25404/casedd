"""Weather alerts/watch-warning widget renderer."""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from casedd.data_store import DataStore, StoreValue
from casedd.renderer.color import parse_color
from casedd.renderer.fonts import get_font
from casedd.renderer.widgets.base import BaseWidget, content_rect, draw_label, fill_background
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig


@dataclass(frozen=True)
class _AlertCtx:
    """Shared drawing context for alert paint helpers."""

    draw: ImageDraw.ImageDraw
    inner: Rect
    body_font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    body_color: tuple[int, int, int]
    body_line_h: int
    y: int


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
        label_h = draw_label(draw, inner, cfg.label or "Watches / Warnings", color=(150, 150, 150))
        root = _resolve_root(cfg.source, ".alert_summary")
        count = int(_to_float(data.get(f"{root}.alert_count"), 0.0))
        summary = str(data.get(f"{root}.alert_summary") or "No active alerts")
        watch_warning = str(data.get(f"{root}.watch_warning") or summary)
        alert_level = str(data.get(f"{root}.alert_level") or "none").lower()
        min_dim = max(1, min(inner.w, inner.h))
        requested = max(9, int(cfg.font_size)) if isinstance(cfg.font_size, int) else 9
        auto_body = max(9, min(40, min_dim // 14, inner.w // 30, inner.h // 10))
        body_sz = max(9, min(max(requested, auto_body), inner.w // 18, inner.h // 6))
        count_font = get_font(max(10, min(48, int(body_sz * 1.35))))
        body_font = get_font(body_sz)
        body_line_h = int(body_font.getbbox("Ag")[3] - body_font.getbbox("Ag")[1]) + max(
            2,
            body_sz // 4,
        )
        has_alerts = count > 0
        tone = _alert_tone(cfg.color, has_alerts, alert_level)
        count_text = f"{count} active alert(s)" if has_alerts else "All clear"
        body_color: tuple[int, int, int] = (220, 226, 232) if has_alerts else (165, 176, 186)
        y = inner.y + label_h + 4
        draw.text((inner.x + 4, y), count_text, fill=tone, font=count_font)
        y += int(count_font.getbbox("Ag")[3] - count_font.getbbox("Ag")[1]) + 2
        if not has_alerts:
            _paint_clear(_AlertCtx(draw, inner, body_font, body_color, body_line_h, y))
            return
        _paint_active(
            _AlertCtx(draw, inner, body_font, body_color, body_line_h, y),
            watch_warning,
            summary,
        )


def _resolve_root(source: str | None, suffix: str) -> str:
    """Return the data-store root prefix given the widget source path."""
    prefix = (source or "").strip() or f"weather{suffix}"
    if prefix.endswith(suffix):
        return prefix[: -len(suffix)]
    return prefix.rstrip(".")


def _paint_clear(ctx: _AlertCtx) -> None:
    """Paint the no-active-alerts state lines."""
    y = ctx.y
    if y + ctx.body_line_h <= ctx.inner.y + ctx.inner.h:
        ctx.draw.text(
            (ctx.inner.x + 4, y),
            "No active watches or warnings",
            fill=ctx.body_color,
            font=ctx.body_font,
        )
        y += ctx.body_line_h
    if y + ctx.body_line_h <= ctx.inner.y + ctx.inner.h:
        ctx.draw.text(
            (ctx.inner.x + 4, y),
            "NWS feed monitored continuously",
            fill=(148, 160, 172),
            font=ctx.body_font,
        )


def _paint_active(ctx: _AlertCtx, watch_warning: str, summary: str) -> None:
    """Paint active alert text lines."""
    wrapped = _wrap_lines(ctx.body_font, watch_warning, max_width=max(12, ctx.inner.w - 8))
    max_lines = max(2, (ctx.inner.y + ctx.inner.h - ctx.y) // ctx.body_line_h)
    y = ctx.y
    for line in wrapped[:max_lines]:
        ctx.draw.text((ctx.inner.x + 4, y), line, fill=ctx.body_color, font=ctx.body_font)
        y += ctx.body_line_h
    if summary and summary != watch_warning and y + ctx.body_line_h < ctx.inner.y + ctx.inner.h:
        trimmed = _truncate_to_width(ctx.body_font, summary, max_width=max(12, ctx.inner.w - 8))
        ctx.draw.text(
            (ctx.inner.x + 4, y), trimmed, fill=(172, 182, 192), font=ctx.body_font
        )


def _alert_tone(
    base_color: str | None,
    has_alerts: bool,
    alert_level: str,
) -> tuple[int, int, int]:
    """Choose alert accent color based on active severity."""
    if not has_alerts:
        return (140, 170, 150)

    if alert_level == "warning":
        return (238, 104, 92)
    if alert_level == "watch":
        return (242, 168, 84)
    if alert_level == "advisory":
        return (248, 206, 116)
    return parse_color(base_color, fallback=(228, 96, 96))


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


def _truncate_to_width(
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    text: str,
    max_width: int,
) -> str:
    """Truncate one line to max pixel width with ellipsis."""
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
