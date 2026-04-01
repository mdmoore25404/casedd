"""Font loading and auto-scaling for CASEDD.

Loads PIL ``ImageFont`` objects and provides a helper that finds the largest
font size that fits a given string within a bounding rectangle.

PIL's default bitmap font is used as a fallback when no TrueType font is
found on the system. For production use, DejaVu fonts (available on most
Linux systems) are tried first.

Public API:
    - :func:`get_font` — load a font at a specific size
    - :func:`fit_font` — find the largest size that fits text in a rect
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import ImageFont

_log = logging.getLogger(__name__)

# Ordered list of font paths to try. First match wins.
_FONT_SEARCH: list[Path] = [
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    Path("/usr/share/fonts/truetype/freefont/FreeSans.ttf"),
]

# Resolved once at module import time — avoids repeated filesystem probing
_FONT_PATH: Path | None = next((p for p in _FONT_SEARCH if p.exists()), None)

if _FONT_PATH:
    _log.debug("Using font: %s", _FONT_PATH)
else:
    _log.warning(
        "No TrueType font found in expected paths — using PIL default bitmap font. "
        "Install 'fonts-dejavu-core' for better rendering."
    )

# Simple LRU cache keyed by (font_path, size) to avoid repeated disk I/O
_font_cache: dict[tuple[Path | None, int], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}

# Cache for fit_font() selected size keyed by content + constraints.
_fit_cache: dict[tuple[str, int, int, int, int], int] = {}


def get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load (or retrieve from cache) a font at the given point size.

    Args:
        size: Desired font size in points. Must be >= 1.

    Returns:
        A PIL font object, either :class:`PIL.ImageFont.FreeTypeFont` (TrueType)
        or :class:`PIL.ImageFont.ImageFont` (built-in bitmap fallback).
    """
    size = max(1, size)
    key = (_FONT_PATH, size)
    if key in _font_cache:
        return _font_cache[key]

    if _FONT_PATH is not None:
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont = ImageFont.truetype(
            str(_FONT_PATH), size
        )
    else:
        font = ImageFont.load_default()

    # Limit cache size to avoid unbounded memory growth during long runs
    if len(_font_cache) > 256:
        _font_cache.clear()

    _font_cache[key] = font
    return font


def fit_font(
    text: str,
    max_w: int,
    max_h: int,
    min_size: int = 8,
    max_size: int = 200,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Binary-search for the largest font size that fits ``text`` in a box.

    Args:
        text: The string to be rendered.
        max_w: Maximum width in pixels.
        max_h: Maximum height in pixels.
        min_size: Smallest font size to try (default: 8).
        max_size: Largest font size to try (default: 200).

    Returns:
        The largest font that fits, or the ``min_size`` font if nothing fits.
    """
    safe_w = max(1, max_w)
    safe_h = max(1, max_h)
    safe_min = max(1, min(min_size, max_size))
    safe_max = max(safe_min, max_size)

    cache_key = (text, safe_w, safe_h, safe_min, safe_max)
    cached_size = _fit_cache.get(cache_key)
    if cached_size is not None:
        return get_font(cached_size)

    lo, hi = safe_min, safe_max
    best_size = safe_min

    while lo <= hi:
        mid = (lo + hi) // 2
        font = get_font(mid)
        bbox = font.getbbox(text)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        if w <= safe_w and h <= safe_h:
            best_size = mid
            lo = mid + 1
        else:
            hi = mid - 1

    # Keep this bounded to avoid unbounded memory growth for highly variable text.
    if len(_fit_cache) > 1024:
        _fit_cache.clear()
    _fit_cache[cache_key] = best_size

    return get_font(best_size)
