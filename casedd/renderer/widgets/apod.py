"""APOD (Astronomy Picture of the Day) widget renderer.

Displays the NASA APOD image fetched by
:class:`~casedd.getters.apod.ApodGetter`.  The image path is read live
from the data store so the widget automatically updates when the getter
caches a new day's image.

An optional title overlay (``show_title: true`` is not in WidgetConfig yet —
use ``label`` instead to show a static string, or leave the APOD title in
the store and use a paired ``value`` widget).

Example .casedd config:

.. code-block:: yaml

    apod_bg:
      type: apod
      source: apod.image_path
      scale: fill
      color: "#ffffff"

Data source keys consumed:
    - ``apod.image_path`` (str) -- local path to the cached APOD image
    - ``apod.title``      (str) -- title string for optional overlay
    - ``apod.available``  (float) -- 1.0 when image is ready

Public API:
    - :class:`ApodWidget` — renders the APOD image onto a PIL canvas
"""

from __future__ import annotations

import logging

from PIL import Image, ImageDraw

from casedd.data_store import DataStore
from casedd.renderer.color import parse_color
from casedd.renderer.fonts import get_font
from casedd.renderer.widgets.base import BaseWidget, content_rect, fill_background
from casedd.renderer.widgets.image import (  # noqa: PLC2701 -- reuse private helpers
    _load_image,
    _scale_image,
)
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig

_log = logging.getLogger(__name__)


class ApodWidget(BaseWidget):
    """Render the NASA Astronomy Picture of the Day image.

    Reads the locally cached image path from the data store (written by
    :class:`~casedd.getters.apod.ApodGetter`) and scales it to the widget's
    bounding rect using the configured ``scale`` mode.

    If no image is available yet, the background is filled with the
    widget background colour and a small status message is rendered.
    """

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        _state: dict[str, object],
    ) -> None:
        """Paint the APOD image onto ``img`` within ``rect``.

        Args:
            img: Canvas image, modified in-place.
            rect: Allocated bounding box for this widget.
            cfg: Widget configuration.  ``cfg.scale`` and ``cfg.color`` are
                respected; ``cfg.source`` overrides the default ``apod.image_path``
                store key.
            data: Live data store.
            _state: Per-widget state dict (unused).
        """
        fill_background(img, rect, cfg.background)
        inner = content_rect(rect, cfg.padding)

        # Resolve the image path key — default to apod.image_path.
        path_key = cfg.source.strip() if cfg.source else "apod.image_path"
        image_path_raw = data.get(path_key)
        image_path = str(image_path_raw).strip() if image_path_raw is not None else ""

        if not image_path:
            _render_placeholder(img, inner, cfg.color)
            return

        source = _load_image(image_path)
        if source is None:
            _render_placeholder(img, inner, cfg.color)
            return

        scaled = _scale_image(source, inner.w, inner.h, cfg.scale)
        img.paste(scaled, (inner.x, inner.y))

        # Optional title overlay drawn at the bottom of the widget.
        if cfg.label:
            _render_title_overlay(img, inner, cfg.label, cfg.color)
        else:
            title_raw = data.get("apod.title")
            if title_raw is not None and str(title_raw).strip():
                _render_title_overlay(img, inner, str(title_raw).strip(), cfg.color)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _render_placeholder(img: Image.Image, rect: Rect, color: str | None) -> None:
    """Draw a simple 'APOD loading...' placeholder in the widget area.

    Args:
        img: Canvas to draw on.
        rect: Bounding rectangle.
        color: Accent colour for the placeholder text.
    """
    draw = ImageDraw.Draw(img)
    accent = parse_color(color, fallback=(120, 160, 220))
    font = get_font(13)
    text = "APOD loading..."
    bb = draw.textbbox((0, 0), text, font=font)
    x = rect.x + (rect.w - int(bb[2] - bb[0])) // 2
    y = rect.y + (rect.h - int(bb[3] - bb[1])) // 2
    draw.text((x, y), text, fill=accent, font=font)


def _render_title_overlay(
    img: Image.Image,
    rect: Rect,
    title: str,
    color: str | None,
) -> None:
    """Draw a semi-transparent title bar at the bottom of the widget.

    Args:
        img: Canvas to draw on.
        rect: Widget bounding rectangle (already padded).
        title: Title string to render.
        color: Accent colour for the title text.
    """
    font = get_font(12)
    draw = ImageDraw.Draw(img)
    bb = draw.textbbox((0, 0), title, font=font)
    text_h = int(bb[3] - bb[1])
    bar_h = text_h + 8
    bar_top = rect.y + rect.h - bar_h

    # Semi-transparent dark bar — composite onto the existing image.
    overlay = Image.new("RGBA", (rect.w, bar_h), (0, 0, 0, 160))
    base_crop = img.crop((rect.x, bar_top, rect.x + rect.w, rect.y + rect.h)).convert("RGBA")
    composited = Image.alpha_composite(base_crop, overlay)
    img.paste(composited.convert("RGB"), (rect.x, bar_top))

    accent = parse_color(color, fallback=(220, 225, 235))
    draw.text((rect.x + 6, bar_top + 4), title, fill=accent, font=font)
