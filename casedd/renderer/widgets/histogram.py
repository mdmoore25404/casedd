"""Histogram widget renderer.

Maintains a rolling sample buffer and draws a bar chart of recent history.

Example .casedd config:

.. code-block:: yaml

    ram_hist:
      type: histogram
      source: memory.percent
      samples: 60
      label: "RAM History"
      color: "#4d96ff"
      min: 0
      max: 100
"""

from __future__ import annotations

from collections import deque
import time

from PIL import Image, ImageDraw, ImageFont

from casedd.data_store import DataStore
from casedd.renderer.color import interpolate_color_stops, parse_color
from casedd.renderer.fonts import get_font
from casedd.renderer.widgets.base import (
    BaseWidget,
    content_rect,
    draw_label,
    fill_background,
    resolve_value,
)
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig

_BAR_BG = (35, 35, 35)
_GAP = 1  # pixel gap between bars
_SERIES_FALLBACK = (
    (34, 204, 136),
    (255, 170, 34),
    (77, 163, 255),
    (203, 141, 255),
)


class HistogramWidget(BaseWidget):
    """Renders a rolling bar chart of sampled values.

    Maintains its own ``deque`` in ``state`` keyed by widget name.
    """

    def draw(  # noqa: PLR0912,PLR0915 -- widget rendering intentionally explicit for hot-path clarity
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        state: dict[str, object],
    ) -> None:
        """Paint the histogram onto ``img``.

        Updates the internal sample buffer from the current data store value.

        Args:
            img: Canvas image.
            rect: Widget bounding box.
            cfg: Widget configuration.
            data: Live data store.
            state: Mutable state dict — stores the ``deque`` for this widget.
        """
        fill_background(img, rect, cfg.background)
        inner = content_rect(rect, cfg.padding)
        draw = ImageDraw.Draw(img)

        label_h = 0
        if cfg.label:
            label_h = draw_label(draw, inner, cfg.label, color=(150, 150, 150))

        if cfg.sources:
            self._draw_multi_series(draw, inner, label_h, cfg, data, state)
            return

        # Initialise or retrieve the rolling sample buffer
        buf_key = "buf"
        if buf_key not in state:
            state[buf_key] = deque[tuple[float, float]]()
        buf: deque[tuple[float, float]] = state[buf_key]  # type: ignore[assignment]

        # Sample current value
        raw = resolve_value(cfg, data)
        now = time.monotonic()
        sampled_value: float | None = None
        if raw is not None:
            try:
                sampled_value = float(raw)
            except (ValueError, TypeError):
                sampled_value = None

        if sampled_value is not None:
            buf.append((now, max(cfg.min, min(cfg.max, sampled_value))))
        while len(buf) > cfg.samples:
            buf.popleft()
        if cfg.window_seconds is not None:
            cutoff = now - cfg.window_seconds
            while buf and buf[0][0] < cutoff:
                buf.popleft()

        values = [sample for _, sample in buf]
        if not values:
            return

        # Draw background track
        area_x = inner.x + 2
        area_w = inner.w - 4
        area_h_total = inner.h - label_h - 4
        current_text = self._format_value_text(values[-1], cfg)
        current_font = get_font(max(9, area_h_total // 4))
        current_bbox = draw.textbbox((0, 0), current_text, font=current_font)
        current_band_h = min(
            max(14, current_bbox[3] - current_bbox[1] + 6),
            max(14, area_h_total // 3),
        )
        current_band_h = int(current_band_h)

        area_y = inner.y + label_h + 2 + current_band_h
        area_h = max(1, area_h_total - current_band_h)
        draw.rectangle([area_x, area_y, area_x + area_w, area_y + area_h], fill=_BAR_BG)

        count = max(1, len(values))
        bar_total_w = area_w / count  # float -- bars may be < 1px wide
        span = cfg.max - cfg.min

        for i, sample in enumerate(values):
            ratio = (sample - cfg.min) / span if span > 0 else 0.0
            bar_h = max(1, int(area_h * ratio))
            bx = area_x + int(i * bar_total_w)
            bw = max(1, int(bar_total_w) - _GAP)
            by = area_y + area_h - bar_h

            if cfg.color_stops:
                fill_rgb = interpolate_color_stops(sample, cfg.color_stops, cfg.min, cfg.max)
            else:
                fill_rgb = parse_color(cfg.color, fallback=(70, 130, 200))

            draw.rectangle([bx, by, bx + bw, area_y + area_h], fill=fill_rgb)

        self._draw_current_value(
            draw,
            (area_x, inner.y + label_h + 2, area_w, current_band_h),
            cfg,
            values[-1],
            current_font,
        )

    def _draw_multi_series(  # noqa: PLR0913 -- render helper needs explicit context
        self,
        draw: ImageDraw.ImageDraw,
        inner: Rect,
        label_h: int,
        cfg: WidgetConfig,
        data: DataStore,
        state: dict[str, object],
    ) -> None:
        """Draw multi-series histogram bars in a single cell.

        Args:
            draw: PIL drawing context.
            inner: Inset drawing rect.
            label_h: Label area already consumed.
            cfg: Widget configuration.
            data: Live data store.
            state: Per-widget mutable state.
        """
        buffers = self._series_buffers(state, cfg.sources)
        now = time.monotonic()
        current_values = self._append_multi_samples(buffers, cfg, data, now)

        area_x = inner.x + 2
        area_w = inner.w - 4

        area_h_total = inner.h - label_h - 4
        legend = self._multi_legend_text(cfg, current_values)
        legend_font = get_font(max(9, area_h_total // 4))
        legend_bbox = draw.textbbox((0, 0), legend, font=legend_font)
        legend_band_h = min(
            max(14, legend_bbox[3] - legend_bbox[1] + 6),
            max(14, area_h_total // 3),
        )
        legend_band_h = int(legend_band_h)

        area_y = inner.y + label_h + 2 + legend_band_h
        area_h = max(1, area_h_total - legend_band_h)
        draw.rectangle([area_x, area_y, area_x + area_w, area_y + area_h], fill=_BAR_BG)

        samples = self._series_sample_count(buffers, cfg.sources)
        if samples <= 0:
            return

        span = cfg.max - cfg.min
        group_w = area_w / samples
        series_count = len(cfg.sources)

        for sample_idx in range(samples):
            group_left = area_x + int(sample_idx * group_w)
            group_total_w = max(1, int(group_w) - _GAP)
            sub_w = max(1, group_total_w // max(1, series_count))
            for series_idx, source in enumerate(cfg.sources):
                sample = self._buffer_value_at(buffers[source], sample_idx, samples)
                if sample is None:
                    continue
                ratio = (sample - cfg.min) / span if span > 0 else 0.0
                bar_h = max(1, int(area_h * ratio))
                bx = group_left + series_idx * sub_w
                by = area_y + area_h - bar_h
                fill = self._series_color(cfg, series_idx)
                draw.rectangle([bx, by, bx + sub_w, area_y + area_h], fill=fill)

        draw.text(
            (area_x + 3, inner.y + label_h + 4),
            legend,
            fill=(220, 220, 220),
            font=legend_font,
        )

    def _series_buffers(
        self,
        state: dict[str, object],
        sources: list[str],
    ) -> dict[str, deque[tuple[float, float]]]:
        """Initialise or retrieve per-source rolling sample buffers."""
        key = "multi_buf"
        if key not in state:
            state[key] = {source: deque[tuple[float, float]]() for source in sources}
        raw_buffers = state[key]
        if not isinstance(raw_buffers, dict):
            fresh = {source: deque[tuple[float, float]]() for source in sources}
            state[key] = fresh
            return fresh
        buffers: dict[str, deque[tuple[float, float]]] = {}
        for source in sources:
            buf = raw_buffers.get(source)
            if isinstance(buf, deque):
                buffers[source] = buf
            else:
                buffers[source] = deque[tuple[float, float]]()
                raw_buffers[source] = buffers[source]
        return buffers

    def _append_multi_samples(
        self,
        buffers: dict[str, deque[tuple[float, float]]],
        cfg: WidgetConfig,
        data: DataStore,
        now: float,
    ) -> dict[str, float | None]:
        """Append one sample per source and trim buffers."""
        current_values: dict[str, float | None] = {}
        cutoff = now - cfg.window_seconds if cfg.window_seconds is not None else None

        for source, buf in buffers.items():
            raw = data.get(source)
            parsed: float | None = None
            try:
                if raw is not None:
                    parsed = float(raw)
            except (ValueError, TypeError):
                parsed = None

            if parsed is not None:
                clamped = max(cfg.min, min(cfg.max, parsed))
                buf.append((now, clamped))
            while len(buf) > cfg.samples:
                buf.popleft()
            if cutoff is not None:
                while buf and buf[0][0] < cutoff:
                    buf.popleft()
            current_values[source] = buf[-1][1] if buf else None
        return current_values

    def _series_sample_count(
        self,
        buffers: dict[str, deque[tuple[float, float]]],
        sources: list[str],
    ) -> int:
        """Return max available sample count across series."""
        if not sources:
            return 0
        return max(len(buffers[source]) for source in sources)

    def _buffer_value_at(
        self,
        buf: deque[tuple[float, float]],
        idx: int,
        total_samples: int,
    ) -> float | None:
        """Return sample value at a shared index, right-aligned by history age."""
        if not buf:
            return None
        left_pad = max(0, total_samples - len(buf))
        if idx < left_pad:
            return None
        local_idx = idx - left_pad
        return list(buf)[local_idx][1]

    def _series_color(self, cfg: WidgetConfig, index: int) -> tuple[int, int, int]:
        """Resolve color for one series in multi-series mode."""
        if index < len(cfg.series_colors):
            return parse_color(
                cfg.series_colors[index],
                fallback=_SERIES_FALLBACK[index % len(_SERIES_FALLBACK)],
            )
        if cfg.color_stops:
            return interpolate_color_stops(cfg.max, cfg.color_stops, cfg.min, cfg.max)
        if cfg.color:
            return parse_color(cfg.color, fallback=_SERIES_FALLBACK[index % len(_SERIES_FALLBACK)])
        return _SERIES_FALLBACK[index % len(_SERIES_FALLBACK)]

    def _multi_legend_text(
        self,
        cfg: WidgetConfig,
        current_values: dict[str, float | None],
    ) -> str:
        """Build compact legend text for current multi-series values."""
        entries: list[str] = []
        for idx, source in enumerate(cfg.sources):
            if idx < len(cfg.series_labels):
                label = cfg.series_labels[idx]
            else:
                label = source.split(".")[-1]
            val = current_values.get(source)
            if val is None:
                text = f"{label} --"
            else:
                text = f"{label} {val:.{cfg.precision}f}"
                if cfg.unit:
                    text = f"{text}{cfg.unit}"
            entries.append(text)
        return "  ".join(entries)

    def _draw_current_value(
        self,
        draw: ImageDraw.ImageDraw,
        area: tuple[int, int, int, int],
        cfg: WidgetConfig,
        current: float,
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
    ) -> None:
        """Draw the latest sampled value at the top-right of the histogram area."""
        area_x, area_y, area_w, area_h = area
        value_text = self._format_value_text(current, cfg)
        text_color = self._value_text_color(current, cfg)

        bbox = draw.textbbox((0, 0), value_text, font=font)
        text_w = bbox[2] - bbox[0]
        text_x = area_x + max(2, area_w - text_w - 3)
        text_h = bbox[3] - bbox[1]
        text_y = area_y + max(1, (area_h - text_h) // 2)
        draw.text((text_x, text_y), value_text, fill=text_color, font=font)

    def _format_value_text(self, current: float, cfg: WidgetConfig) -> str:
        """Format the latest sampled value using widget precision and optional unit."""
        value_text = f"{current:.{cfg.precision}f}"
        if cfg.unit:
            return f"{value_text} {cfg.unit}"
        return value_text

    def _value_text_color(self, current: float, cfg: WidgetConfig) -> tuple[int, int, int]:
        """Resolve text color for the latest sampled value."""
        if cfg.color_stops:
            return interpolate_color_stops(current, cfg.color_stops, cfg.min, cfg.max)
        return parse_color(cfg.color, fallback=(180, 190, 205))
