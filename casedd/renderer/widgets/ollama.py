"""Ollama running-model dashboard widget renderer.

Renders a compact table of currently loaded models from detailed getter keys:
- ``<prefix>.models.local_count``
- ``<prefix>.models.running_count``
- ``<prefix>.version``
- ``<prefix>.running_<n>.name``
- ``<prefix>.running_<n>.size_vram_bytes``
- ``<prefix>.running_<n>.ttl``
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw

from casedd.data_store import DataStore, StoreValue
from casedd.renderer.color import parse_color
from casedd.renderer.fonts import get_font
from casedd.renderer.widgets.base import BaseWidget, content_rect, fill_background
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig


@dataclass(frozen=True)
class _RunningRow:
    """One running-model row rendered by the widget."""

    name: str
    vram_gb: float
    ttl: str


def _to_float(value: StoreValue | None, default: float = 0.0) -> float:
    """Return a store value as float with fallback."""
    if value is None:
        return default
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _to_text(value: StoreValue | None, default: str = "") -> str:
    """Return a store value as display text with fallback."""
    if value is None:
        return default
    return str(value)


def _iter_running_rows(data: DataStore, prefix: str, max_items: int) -> list[_RunningRow]:
    """Load running-model rows from enumerated ``running_<n>`` keys."""
    rows: list[_RunningRow] = []
    for index in range(1, max_items + 1):
        base = f"{prefix}.running_{index}"
        name = _to_text(data.get(f"{base}.name")).strip()
        if not name:
            break
        vram_bytes = _to_float(data.get(f"{base}.size_vram_bytes"), 0.0)
        rows.append(
            _RunningRow(
                name=name,
                vram_gb=max(0.0, vram_bytes / 1_000_000_000),
                ttl=_to_text(data.get(f"{base}.ttl"), "n/a"),
            )
        )
    return rows


class OllamaWidget(BaseWidget):
    """Render a single-node Ollama runtime dashboard card."""

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        _state: dict[str, object],
    ) -> None:
        """Paint the Ollama dashboard card into the widget rect."""
        fill_background(img, rect, cfg.background)
        inner = content_rect(rect, cfg.padding)
        draw = ImageDraw.Draw(img)

        prefix = cfg.source.strip() if cfg.source else "ollama"
        prefix = prefix.removesuffix(".")

        title = cfg.label if cfg.label else "Ollama"
        accent = parse_color(cfg.color, fallback=(126, 201, 166))
        title_font = get_font(max(12, inner.h // 11))
        body_font = get_font(max(10, inner.h // 16))
        small_font = get_font(max(9, inner.h // 20))

        draw.text((inner.x + 2, inner.y + 2), title, fill=accent, font=title_font)

        version = _to_text(data.get(f"{prefix}.version"), "?")
        local_count = int(_to_float(data.get(f"{prefix}.models.local_count"), 0.0))
        running_count = int(_to_float(data.get(f"{prefix}.models.running_count"), 0.0))
        summary = f"v{version}  local:{local_count}  running:{running_count}"
        summary_y = inner.y + max(18, inner.h // 10)
        draw.text((inner.x + 2, summary_y), summary, fill=(210, 220, 230), font=small_font)

        rows = _iter_running_rows(data, prefix, cfg.max_items if cfg.max_items is not None else 6)

        head_y = summary_y + max(14, inner.h // 12)
        draw.text((inner.x + 2, head_y), "MODEL", fill=(168, 182, 196), font=small_font)
        draw.text((inner.x + inner.w - 95, head_y), "VRAM", fill=(168, 182, 196), font=small_font)
        draw.text((inner.x + inner.w - 46, head_y), "TTL", fill=(168, 182, 196), font=small_font)

        line_y = head_y + max(10, inner.h // 22)
        draw.line((inner.x + 1, line_y, inner.x + inner.w - 1, line_y), fill=(58, 68, 80), width=1)

        bb = draw.textbbox((0, 0), "Ag", font=body_font)
        row_h = int(bb[3] - bb[1]) + 4
        y = line_y + 4

        if not rows:
            draw.text((inner.x + 2, y), "No running models", fill=(140, 150, 160), font=body_font)
            return

        for row in rows:
            if y + row_h > inner.y + inner.h:
                break
            model_name = row.name.removesuffix(":latest")
            draw.text((inner.x + 2, y), model_name[:28], fill=(230, 235, 240), font=body_font)
            draw.text(
                (inner.x + inner.w - 95, y),
                f"{row.vram_gb:.1f}G",
                fill=(190, 205, 220),
                font=body_font,
            )
            draw.text(
                (inner.x + inner.w - 46, y),
                row.ttl,
                fill=(173, 188, 205),
                font=body_font,
            )
            y += row_h
