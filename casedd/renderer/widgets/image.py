"""Image widget renderer.

Loads a static image from disk and renders it scaled into the bounding box.
The loaded image is cached per path to avoid repeated disk I/O on each frame.

Example .casedd config:

.. code-block:: yaml

    logo:
      type: image
      path: "assets/logo.png"
      scale: fit
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

from casedd.data_store import DataStore
from casedd.renderer.widgets.base import BaseWidget, fill_background
from casedd.template.grid import Rect
from casedd.template.models import ScaleMode, WidgetConfig

_log = logging.getLogger(__name__)

# Module-level image cache: path string → PIL Image (converted to RGB)
_image_cache: dict[str, Image.Image] = {}


def _load_image(path_str: str) -> Image.Image | None:
    """Load and cache an image from disk.

    Args:
        path_str: Filesystem path to the image file.

    Returns:
        A PIL Image in RGB mode, or ``None`` if the file cannot be loaded.
    """
    if path_str in _image_cache:
        return _image_cache[path_str]

    path = Path(path_str)
    if not path.is_file():
        _log.warning("Image widget: file not found: %s", path)
        return None

    try:
        loaded = Image.open(path).convert("RGB")
    except OSError as exc:
        _log.warning("Image widget: cannot open '%s': %s", path, exc)
        return None

    _image_cache[path_str] = loaded
    return loaded


def _scale_image(source: Image.Image, w: int, h: int, mode: ScaleMode) -> Image.Image:
    """Scale a source image to fit the target dimensions according to ``mode``.

    Args:
        source: The source PIL image.
        w: Target width in pixels.
        h: Target height in pixels.
        mode: Scaling mode (fit / fill / stretch).

    Returns:
        A new PIL image of exactly (w, h) pixels.
    """
    sw, sh = source.size

    if mode == ScaleMode.STRETCH:
        return source.resize((w, h), Image.LANCZOS)  # type: ignore[attr-defined]

    # Compute scale factor
    scale = min(w / sw, h / sh) if mode == ScaleMode.FIT else max(w / sw, h / sh)
    new_w = int(sw * scale)
    new_h = int(sh * scale)
    scaled = source.resize((new_w, new_h), Image.LANCZOS)  # type: ignore[attr-defined]

    # Center-crop or pad to exact target size
    if mode == ScaleMode.FILL:
        left = (new_w - w) // 2
        top = (new_h - h) // 2
        return scaled.crop((left, top, left + w, top + h))

    # FIT — center on a black canvas
    canvas = Image.new("RGB", (w, h), (0, 0, 0))
    offset_x = (w - new_w) // 2
    offset_y = (h - new_h) // 2
    canvas.paste(scaled, (offset_x, offset_y))
    return canvas


class ImageWidget(BaseWidget):
    """Renders a static image from disk, scaled to the bounding box."""

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        _data: DataStore,
        _state: dict[str, object],
    ) -> None:
        """Paint the image onto the canvas.

        Args:
            img: Canvas image.
            rect: Widget bounding box.
            cfg: Widget configuration (``cfg.path`` and ``cfg.scale`` used).
            _data: Unused -- image is static.
            state: Unused for this widget type.
        """
        fill_background(img, rect, cfg.background)

        if cfg.path is None:
            _log.warning("Image widget has no 'path' configured.")
            return

        source = _load_image(cfg.path)
        if source is None:
            return

        scaled = _scale_image(source, rect.w, rect.h, cfg.scale)
        img.paste(scaled, (rect.x, rect.y))
