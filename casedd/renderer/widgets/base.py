"""Abstract base class and shared helpers for widget renderers.

Public API:
    - :class:`BaseWidget` — subclass and implement :meth:`draw`
    - :func:`resolve_value` — look up a widget's live value from the data store
    - :func:`draw_label` — paint a small label string at the top of a rect
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from PIL import Image, ImageDraw

from casedd.data_store import DataStore, StoreValue
from casedd.renderer.color import RGBTuple, parse_color
from casedd.renderer.fonts import fit_font, get_font
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig


class BaseWidget(ABC):
    """Abstract renderer for a single widget type.

    Subclasses are stateless by convention — all state (e.g. rolling history
    for histograms) is held externally in a per-widget state dict passed through
    the engine.
    """

    @abstractmethod
    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        state: dict[str, object],
    ) -> None:
        """Paint this widget onto ``img`` within ``rect``.

        Args:
            img: The canvas being rendered (modified in-place).
            rect: Pixel bounding box allocated to this widget.
            cfg: Widget configuration from the parsed template.
            data: Live data store (read-only from widget perspective).
            state: Per-widget mutable state dict (e.g. history buffers).
                   The engine creates one per widget name and passes it here.
        """
        ...


def resolve_value(cfg: WidgetConfig, data: DataStore) -> StoreValue | None:
    """Resolve a widget's display value from its config and the data store.

    Checks ``cfg.content`` first (literal static value), then ``cfg.source``
    (live data store lookup).

    Args:
        cfg: Widget configuration.
        data: Live data store.

    Returns:
        The resolved value, or ``None`` if neither ``content`` nor ``source``
        is configured, or the ``source`` key is not yet in the store.
    """
    if cfg.content is not None:
        return cfg.content
    if cfg.source is not None:
        return data.get(cfg.source)
    return None


def draw_label(
    draw: ImageDraw.ImageDraw,
    rect: Rect,
    label: str,
    color: RGBTuple,
    label_size: int = 11,
) -> int:
    """Draw a small label string at the top edge of ``rect``.

    Args:
        draw: PIL ImageDraw instance.
        rect: Bounding box for the widget.
        label: Label text to draw.
        color: Label text color.
        label_size: Font size for the label (default: 11).

    Returns:
        Height of the label in pixels (so callers can reduce available height).
    """
    font = get_font(label_size)
    bbox = font.getbbox(label)
    lw = bbox[2] - bbox[0]
    lh = bbox[3] - bbox[1]
    x = rect.x + (rect.w - lw) // 2  # horizontally centered
    draw.text((x, rect.y + 2), label, fill=color, font=font)
    return int(lh) + 4  # 2px top padding + 2px bottom gap


def draw_value_text(  # noqa: PLR0913 — helper genuinely needs all parameters; a config dataclass would add ceremony without benefit
    draw: ImageDraw.ImageDraw,
    rect: Rect,
    text: str,
    color: RGBTuple,
    font_size: int | str,
    label_offset: int = 0,
) -> None:
    """Draw a centered value string within a rect, with optional auto-scaling.

    Args:
        draw: PIL ImageDraw instance.
        rect: Bounding box for the value text.
        text: The string to render.
        color: Text color.
        font_size: Point size or ``"auto"`` to scale to fill the rect.
        label_offset: Vertical pixels already consumed by a label above.
    """
    available_h = rect.h - label_offset
    available_w = rect.w - 8  # 4px left + 4px right padding

    if font_size == "auto":
        font = fit_font(text, available_w, available_h - 4)
    else:
        font = get_font(int(font_size))

    bbox = font.getbbox(text)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = rect.x + (rect.w - tw) // 2
    y = rect.y + label_offset + (available_h - th) // 2
    draw.text((x, y), text, fill=color, font=font)


def fill_background(img: Image.Image, rect: Rect, color: str | None) -> None:
    """Fill the widget bounding box with a background color if specified.

    Args:
        img: Canvas image (modified in-place).
        rect: Bounding box to fill.
        color: Background color string, or ``None`` for no fill.
    """
    if color is None:
        return
    rgb = parse_color(color)
    # Create a solid color overlay and paste it
    overlay = Image.new("RGB", (rect.w, rect.h), rgb)
    img.paste(overlay, (rect.x, rect.y))


def _normalize_padding(padding: int | list[int]) -> tuple[int, int, int, int]:
    """Normalize widget padding to a (top, right, bottom, left) tuple.

    Supports CSS-like shorthand:
    - int: all four sides
    - [vertical, horizontal]
    - [top, right, bottom, left]

    Any other list shape is treated as no padding.

    Args:
        padding: Widget padding value from config.

    Returns:
        Normalized integer padding tuple.
    """
    if isinstance(padding, int):
        p = max(0, padding)
        return (p, p, p, p)

    if len(padding) == 2:
        v = max(0, int(padding[0]))
        h = max(0, int(padding[1]))
        return (v, h, v, h)

    if len(padding) == 4:
        return tuple(max(0, int(v)) for v in padding)  # type: ignore[return-value]

    return (0, 0, 0, 0)


def content_rect(rect: Rect, padding: int | list[int]) -> Rect:
    """Return an inset rect for widget drawing content.

    Args:
        rect: Outer widget rectangle.
        padding: Padding value from widget config.

    Returns:
        Inset rectangle with non-negative size.
    """
    top, right, bottom, left = _normalize_padding(padding)
    x = rect.x + left
    y = rect.y + top
    w = max(1, rect.w - left - right)
    h = max(1, rect.h - top - bottom)
    return Rect(x=x, y=y, w=w, h=h)
