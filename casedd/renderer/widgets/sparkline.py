"""Sparkline widget renderer.

Maintains a rolling sample buffer and draws a smooth line chart with no axes.

Example .casedd config:

.. code-block:: yaml

    net_in:
      type: sparkline
      source: net.bytes_recv_rate
      samples: 60
      label: "↓ MB/s"
      color: "#6bcb77"
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import time

from PIL import Image, ImageDraw, ImageFont

from casedd.data_store import DataStore
from casedd.renderer.color import parse_color
from casedd.renderer.widgets.base import (
    BaseWidget,
    choose_font_for_box,
    content_rect,
    draw_label,
    fill_background,
    resolve_value,
)
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig

# Absolute upper bound on history deque length regardless of per-widget cfg.samples.
# Prevents runaway memory growth if a template configures an unreasonably large
# samples value and the daemon runs for hours without restart.
_MAX_HISTORY_SAMPLES: int = 600  # 5 min at the default 2 Hz render rate

_AREA_ALPHA = 60   # alpha of the filled area under the line (0-255)
_SERIES_FALLBACK = (
    (34, 204, 136),
    (255, 170, 34),
    (77, 163, 255),
    (203, 141, 255),
)


@dataclass(frozen=True)
class _LegendEntry:
    """Legend text and resolved color for one multi-series sparkline entry."""

    text: str
    color: tuple[int, int, int]


def _update_buffer(
    buf: deque[tuple[float, float]],
    value: float | None,
    samples: int,
    window_seconds: float | None,
) -> list[float]:
    """Append one sample to the buffer and return current values.

    Args:
        buf: Timestamped sample deque.
        value: Latest numeric value, or ``None`` to skip appending.
        samples: Max number of samples to retain.
        window_seconds: Optional age-based retention window.

    Returns:
        List of sample values currently in the deque.
    """
    now = time.monotonic()
    if value is not None:
        buf.append((now, max(0.0, value)))
    while len(buf) > samples:
        buf.popleft()

    if window_seconds is not None:
        cutoff = now - window_seconds
        while buf and buf[0][0] < cutoff:
            buf.popleft()

    return [sample for _, sample in buf]


def _build_points(  # noqa: PLR0913 — explicit geometry args keep callsites simple
    samples: list[float],
    area_x: int,
    area_y: int,
    area_w: int,
    area_h: int,
    data_min: float,
    data_max: float,
) -> list[tuple[int, int]]:
    """Build sparkline plot points for the sample list."""
    points: list[tuple[int, int]] = []
    denom = max(1, len(samples) - 1)

    for i, sample in enumerate(samples):
        x = area_x + int(i * area_w / denom)
        ratio = (sample - data_min) / (data_max - data_min) if data_max != data_min else 0.0
        ratio = max(0.0, min(1.0, ratio))
        y = area_y + area_h - int(ratio * area_h)
        points.append((x, y))

    return points


def _draw_filled_area(  # noqa: PLR0913 — explicit geometry args keep helper stateless
    img: Image.Image,
    color: tuple[int, int, int],
    points: list[tuple[int, int]],
    area_x: int,
    area_y: int,
    area_w: int,
    area_h: int,
) -> None:
    """Draw translucent fill under the sparkline curve.

    Works on the sparkline sub-region only rather than the full canvas.
    This cuts per-call memory allocations from ~4x canvas_size to
    ~4x region_size -- a ~20x reduction for a typical dashboard sparkline
    that occupies 1/7 of the display area.
    """
    crop_box = (area_x, area_y, area_x + area_w, area_y + area_h)
    # Translate absolute canvas points to region-local coordinates.
    local_points = [(x - area_x, y - area_y) for x, y in points]
    poly_points = [(0, area_h), *local_points, (area_w, area_h)]
    overlay = Image.new("RGBA", (area_w, area_h), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.polygon(poly_points, fill=(*color, _AREA_ALPHA))
    region = img.crop(crop_box).convert("RGBA")
    img.paste(Image.alpha_composite(region, overlay).convert("RGB"), crop_box)


class SparklineWidget(BaseWidget):
    """Renders a rolling line chart with a filled area underneath."""

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        state: dict[str, object],
    ) -> None:
        """Paint the sparkline onto ``img``.

        Args:
            img: Canvas image.
            rect: Widget bounding box.
            cfg: Widget configuration.
            data: Live data store.
            state: Mutable state dict — stores sample buffer and auto-max.
        """
        fill_background(img, rect, cfg.background)
        inner = content_rect(rect, cfg.padding)
        draw = ImageDraw.Draw(img)

        label_h = 0
        if cfg.label:
            label_h = draw_label(draw, inner, cfg.label, color=(150, 150, 150))

        area_x = inner.x + 2
        area_y = inner.y + label_h + 2
        area_w = inner.w - 4
        area_h = inner.h - label_h - 4

        if cfg.sources:
            self._draw_multi_series(draw, img, cfg, data, state, area_x, area_y, area_w, area_h)
            return

        buf_key = "buf"
        if buf_key not in state:
            # maxlen caps absolute memory usage; _update_buffer still trims
            # to cfg.samples for display resolution.
            state[buf_key] = deque[tuple[float, float]](maxlen=_MAX_HISTORY_SAMPLES)
        buf: deque[tuple[float, float]] = state[buf_key]  # type: ignore[assignment]

        raw = resolve_value(cfg, data)
        parsed: float | None = None
        try:
            if raw is not None:
                parsed = float(raw)
        except (ValueError, TypeError):
            parsed = None

        samples = _update_buffer(buf, parsed, cfg.samples, cfg.window_seconds)
        if not samples:
            return

        # Dynamic max: use the configured max, or auto-scale to buf peak
        data_max = cfg.max if cfg.max > cfg.min else max(*samples, 0.001)
        data_min = cfg.min

        color = parse_color(cfg.color, fallback=(100, 200, 100))

        points = _build_points(samples, area_x, area_y, area_w, area_h, data_min, data_max)
        if len(points) >= 2:
            _draw_filled_area(img, color, points, area_x, area_y, area_w, area_h)

        # Redraw the draw handle after paste (img was modified)
        draw = ImageDraw.Draw(img)

        # Draw a line once history exists, otherwise draw a single-point marker.
        if len(points) >= 2:
            draw.line(points, fill=color, width=2)
        else:
            x, y = points[0]
            draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=color)

        # Current value text in top-right corner
        current_value = samples[-1]
        current_str = f"{current_value:.{cfg.precision}f}"
        if cfg.unit:
            current_str = f"{current_str} {cfg.unit}"
        font = choose_font_for_box(
            current_str,
            max(32, (area_w * 9) // 20),
            max(12, area_h // 4),
            "auto",
            min_size=10,
        )
        bbox = font.getbbox(current_str)
        tw = bbox[2] - bbox[0]
        draw.text((area_x + area_w - tw - 2, area_y + 2), current_str, fill=color, font=font)

    def _draw_multi_series(  # noqa: PLR0912,PLR0913 -- explicit render context keeps widget path clear
        self,
        draw: ImageDraw.ImageDraw,
        img: Image.Image,
        cfg: WidgetConfig,
        data: DataStore,
        state: dict[str, object],
        area_x: int,
        area_y: int,
        area_w: int,
        area_h: int,
    ) -> None:
        """Render multiple source series as overlaid spark lines."""
        key = "multi_buf"
        if key not in state:
            state[key] = {
                source: deque[tuple[float, float]](maxlen=_MAX_HISTORY_SAMPLES)
                for source in cfg.sources
            }

        raw_buffers = state[key]
        if not isinstance(raw_buffers, dict):
            raw_buffers = {
                source: deque[tuple[float, float]](maxlen=_MAX_HISTORY_SAMPLES)
                for source in cfg.sources
            }
            state[key] = raw_buffers

        buffers: dict[str, deque[tuple[float, float]]] = {}
        cutoff = time.monotonic() - cfg.window_seconds if cfg.window_seconds is not None else None
        current_values: dict[str, float | None] = {}

        for source in cfg.sources:
            buf = raw_buffers.get(source)
            if not isinstance(buf, deque):
                buf = deque[tuple[float, float]](maxlen=_MAX_HISTORY_SAMPLES)
                raw_buffers[source] = buf
            buffers[source] = buf

            raw = data.get(source)
            parsed: float | None = None
            try:
                if raw is not None:
                    parsed = float(raw)
            except (ValueError, TypeError):
                parsed = None

            _update_buffer(buf, parsed, cfg.samples, cfg.window_seconds)
            if cutoff is not None:
                while buf and buf[0][0] < cutoff:
                    buf.popleft()
            current_values[source] = buf[-1][1] if buf else None

        all_samples = [val for buf in buffers.values() for _, val in buf]
        if not all_samples:
            return

        data_min = cfg.min
        data_max = cfg.max if cfg.max > cfg.min else max(*all_samples, 0.001)

        for idx, source in enumerate(cfg.sources):
            series = [sample for _, sample in buffers[source]]
            if len(series) < 2:
                continue
            color = self._series_color(cfg, idx)
            points = _build_points(series, area_x, area_y, area_w, area_h, data_min, data_max)
            draw.line(points, fill=color, width=2)

            # Light area fill only for the first series to reduce visual clutter.
            if idx == 0:
                _draw_filled_area(img, color, points, area_x, area_y, area_w, area_h)
                draw = ImageDraw.Draw(img)

        legend_entries = self._legend_entries(cfg, current_values)

        if legend_entries:
            legend = "  ".join(entry.text for entry in legend_entries)
            font = choose_font_for_box(
                legend,
                max(32, area_w - 4),
                max(12, area_h // 6),
                "auto",
                min_size=10,
            )
            self._draw_legend_entries(draw, area_x + 2, area_y + 2, font, legend_entries)

    def _legend_entries(
        self,
        cfg: WidgetConfig,
        current_values: dict[str, float | None],
    ) -> list[_LegendEntry]:
        """Build legend entries so labels visibly match their series colors."""
        entries: list[_LegendEntry] = []
        for idx, source in enumerate(cfg.sources):
            label = (
                cfg.series_labels[idx]
                if idx < len(cfg.series_labels)
                else source.split(".")[-1]
            )
            current = current_values.get(source)
            if current is None:
                text = f"{label} --"
            else:
                value_text = f"{current:.{cfg.precision}f}"
                if cfg.unit:
                    value_text = f"{value_text} {cfg.unit}"
                text = f"{label} {value_text}"
            entries.append(_LegendEntry(text=text, color=self._series_color(cfg, idx)))
        return entries

    def _draw_legend_entries(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
        entries: list[_LegendEntry],
    ) -> None:
        """Draw multi-series legend entries using their corresponding colors."""
        cursor_x = x
        for idx, entry in enumerate(entries):
            if idx > 0:
                cursor_x += 12
            draw.text((cursor_x, y), entry.text, fill=entry.color, font=font)
            bbox = draw.textbbox((cursor_x, y), entry.text, font=font)
            cursor_x += int(bbox[2] - bbox[0])

    def _series_color(self, cfg: WidgetConfig, idx: int) -> tuple[int, int, int]:
        """Resolve per-series color with explicit template overrides."""
        if idx < len(cfg.series_colors):
            return parse_color(
                cfg.series_colors[idx],
                fallback=_SERIES_FALLBACK[idx % len(_SERIES_FALLBACK)],
            )
        if cfg.color:
            return parse_color(cfg.color, fallback=_SERIES_FALLBACK[idx % len(_SERIES_FALLBACK)])
        return _SERIES_FALLBACK[idx % len(_SERIES_FALLBACK)]
