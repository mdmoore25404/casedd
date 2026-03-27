"""Slideshow widget renderer.

Cycles through a list of images or all images in one or more directories,
displaying each for a configurable duration. Supports a fade transition.

Example .casedd config:

.. code-block:: yaml

    bg:
      type: slideshow
      paths:
        - "assets/slideshow/"
      interval: 10
      scale: fill
      transition: fade
"""

from __future__ import annotations

import logging
from pathlib import Path
import time

from PIL import Image

from casedd.data_store import DataStore
from casedd.renderer.widgets.base import BaseWidget, fill_background
from casedd.renderer.widgets.image import _scale_image
from casedd.template.grid import Rect
from casedd.template.models import TransitionMode, WidgetConfig

_log = logging.getLogger(__name__)

# Image file extensions considered valid for slideshow
_IMG_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}

# Fade transition duration in seconds
_FADE_DURATION = 1.0


def _collect_images(paths: list[str]) -> list[Path]:
    """Expand a list of file/directory paths into a sorted list of image files.

    Args:
        paths: List of paths — each may be a file or a directory.

    Returns:
        Sorted list of ``Path`` objects pointing to image files.
    """
    result: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            result.extend(
                sorted(f for f in p.iterdir() if f.suffix.lower() in _IMG_EXTS)
            )
        elif p.is_file() and p.suffix.lower() in _IMG_EXTS:
            result.append(p)
        else:
            _log.warning("Slideshow: path not found or not an image: %s", p)
    return result


class SlideshowWidget(BaseWidget):
    """Cycles through images with an optional fade transition.

    State keys used (stored in ``state`` dict):
        ``files`` (list[Path]): collected image file list
        ``index`` (int): current image index
        ``last_change`` (float): monotonic time of last image switch
        ``current`` (Image.Image | None): current PIL image
        ``next_img`` (Image.Image | None): next image (during fade)
    """

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        _data: DataStore,
        state: dict[str, object],
    ) -> None:
        """Paint the current slideshow frame onto ``img``.

        Args:
            img: Canvas image.
            rect: Widget bounding box.
            cfg: Widget configuration.
            _data: Unused -- slideshow is time-driven.
            state: Mutable state dict for this widget instance.
        """
        fill_background(img, rect, cfg.background)

        # Initialise or refresh the file list on first call
        if "files" not in state:
            state["files"] = _collect_images(cfg.paths)
            state["index"] = 0
            state["last_change"] = time.monotonic()
            state["current"] = None
            state["next_img"] = None

        files: list[Path] = state["files"]  # type: ignore[assignment]
        if not files:
            return

        now = time.monotonic()
        last_change: float = state["last_change"]  # type: ignore[assignment]
        index: int = state["index"]  # type: ignore[assignment]

        # Check if it's time to advance to the next image
        elapsed = now - last_change
        if elapsed >= cfg.interval:
            state["index"] = (index + 1) % len(files)
            state["last_change"] = now
            state["current"] = None  # force reload
            state["next_img"] = None
            index = state["index"]  # type: ignore[assignment]
            elapsed = 0.0

        # Load current image if not cached
        current: Image.Image | None = state["current"]  # type: ignore[assignment]
        if current is None:
            try:
                raw = Image.open(files[index % len(files)]).convert("RGB")
                current = _scale_image(raw, rect.w, rect.h, cfg.scale)
                state["current"] = current
            except OSError as exc:
                _log.warning("Slideshow: cannot open %s: %s", files[index], exc)
                return

        if cfg.transition == TransitionMode.FADE and elapsed >= cfg.interval - _FADE_DURATION:
            # Fade phase: blend current → next image
            next_index = (index + 1) % len(files)
            next_img: Image.Image | None = state["next_img"]  # type: ignore[assignment]
            if next_img is None:
                try:
                    raw_next = Image.open(files[next_index]).convert("RGB")
                    next_img = _scale_image(raw_next, rect.w, rect.h, cfg.scale)
                    state["next_img"] = next_img
                except OSError:
                    next_img = current

            fade_progress = (elapsed - (cfg.interval - _FADE_DURATION)) / _FADE_DURATION
            alpha = min(1.0, max(0.0, fade_progress))
            blended = Image.blend(current, next_img, alpha)
            img.paste(blended, (rect.x, rect.y))
        else:
            img.paste(current, (rect.x, rect.y))
