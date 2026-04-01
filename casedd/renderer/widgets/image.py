"""Image widget renderer.

Loads a static image from disk and renders it scaled into the bounding box.
The loaded image is cached per path to avoid repeated disk I/O on each frame.

When ``cfg.source`` is set, the widget also accepts a live data-store value
containing either a filesystem path or an HTTP(S) image URL. This keeps the
generic image widget usable for getters that publish changing preview images.

Metric-driven image selection (``tiers``): the widget can display different
images depending on live data-store values.  Tiers are evaluated from
highest (last in the list) to lowest; the first matching tier's image is shown.
When no tier fires the base ``path`` is used.  See :class:`ImageTier`.

Example .casedd config:

.. code-block:: yaml

    mascot:
      type: image
      path: "assets/casedd-logo.png"
      scale: fit
      tiers:
        - path: assets/casedd-logo-stressed.png
          when:
            - { source: cpu.percent,   operator: gte, value: 50 }
            - { source: memory.percent, operator: gte, value: 60 }
        - path: assets/casedd-logo-fire.png
          when:
            - { source: cpu.percent, operator: gte, value: 90 }
"""

from __future__ import annotations

from io import BytesIO
import logging
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from PIL import Image

from casedd.data_store import DataStore, StoreValue
from casedd.renderer.widgets.base import BaseWidget, fill_background
from casedd.template.grid import Rect
from casedd.template.models import ImageTier, ScaleMode, WidgetConfig

_log = logging.getLogger(__name__)

# Module-level image cache: path string → PIL Image (converted to RGBA)
_image_cache: dict[str, Image.Image] = {}

# Comparison dispatch table keyed by operator token.
_OPS: dict[str, object] = {
    "gt":  lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt":  lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "eq":  lambda a, b: a == b,
    "neq": lambda a, b: a != b,
}


def _store_to_float(val: StoreValue) -> float | None:
    """Attempt to coerce a store value to ``float``; return ``None`` on failure.

    Args:
        val: A value as stored in the data store.

    Returns:
        Float representation, or ``None`` if the value cannot be converted.
    """
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(val)
    except ValueError:
        return None


def _tier_fires(tier: ImageTier, snapshot: dict[str, StoreValue]) -> bool:
    """Return ``True`` if any condition in *tier* is satisfied (OR semantics).

    A data key absent from *snapshot* evaluates to ``False`` — the tier stays
    inactive when data for that source has not yet arrived.

    Args:
        tier: The image tier whose conditions are evaluated.
        snapshot: Current data-store snapshot.

    Returns:
        ``True`` when at least one ``when`` condition matches.
    """
    for cond in tier.when:
        raw = snapshot.get(cond.source)
        if raw is None:
            continue
        lhs = _store_to_float(raw)
        rhs = _store_to_float(cond.value)
        if lhs is not None and rhs is not None:
            op_fn = _OPS.get(cond.operator)
            if callable(op_fn) and op_fn(lhs, rhs):
                return True
        elif (
            cond.operator == "eq" and str(raw) == str(cond.value)
        ) or (
            cond.operator == "neq" and str(raw) != str(cond.value)
        ):
            return True
    return False


def _load_image(source_ref: str) -> Image.Image | None:
    """Load and cache an image from disk or a remote URL.

    Args:
        source_ref: Filesystem path or HTTP(S) URL to the image.

    Returns:
        A PIL Image in RGBA mode, or ``None`` if the source cannot be loaded.
    """
    if source_ref in _image_cache:
        return _image_cache[source_ref]

    if source_ref.startswith(("http://", "https://")):
        return _load_remote_image(source_ref)

    path = Path(source_ref)
    if not path.is_file():
        _log.warning("Image widget: file not found: %s", path)
        return None

    try:
        loaded = Image.open(path).convert("RGBA")
    except OSError as exc:
        _log.warning("Image widget: cannot open '%s': %s", path, exc)
        return None

    _image_cache[source_ref] = loaded
    return loaded


def _load_remote_image(url: str) -> Image.Image | None:
    """Fetch and cache an image from a remote HTTP(S) URL."""
    req = Request(url, headers={"User-Agent": "CASEDD/0.2"}, method="GET")  # noqa: S310
    try:
        with urlopen(req, timeout=5) as resp:  # noqa: S310
            raw = resp.read()
    except URLError as exc:
        _log.warning("Image widget: failed to fetch '%s': %s", url, exc)
        return None

    try:
        loaded = Image.open(BytesIO(raw)).convert("RGBA")
    except OSError as exc:
        _log.warning("Image widget: cannot decode remote image '%s': %s", url, exc)
        return None

    _image_cache[url] = loaded
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

    if mode == ScaleMode.FIT:
        return scaled

    # Center-crop or pad to exact target size
    if mode == ScaleMode.FILL:
        left = (new_w - w) // 2
        top = (new_h - h) // 2
        return scaled.crop((left, top, left + w, top + h))

    return scaled


class ImageWidget(BaseWidget):
    """Renders an image from disk or a dynamic source, scaled to the box.

    Supports metric-driven image selection via ``cfg.tiers``: the widget
    evaluates tiers from highest (last) to lowest (first) and displays the
    first tier whose conditions fire.  Falls back to a populated ``cfg.source``
    store value, then ``cfg.path`` when no tier is active.
    """

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        _state: dict[str, object],
    ) -> None:
        """Paint the image onto the canvas.

        When ``cfg.tiers`` is non-empty the widget evaluates each tier (last →
        first) and uses the first matching tier's ``path``.  Falls back to
        ``cfg.path`` when no tier fires.

        Args:
            img: Canvas image.
            rect: Widget bounding box.
            cfg: Widget configuration (``cfg.path``, ``cfg.scale``,
                ``cfg.tiers`` used).
            data: Live data store — read for tier condition evaluation.
            _state: Per-widget state used to cache scaled image variants.
        """
        fill_background(img, rect, cfg.background)

        # Determine active image source, considering metric-driven tiers first.
        active_path: str | None = None
        if cfg.tiers:
            snapshot = data.snapshot()
            # Evaluate highest tier first (last in the list); first match wins.
            for tier in reversed(cfg.tiers):
                if _tier_fires(tier, snapshot):
                    active_path = tier.path
                    break

        if active_path is None and cfg.source:
            raw_value = data.get(cfg.source)
            if isinstance(raw_value, str):
                candidate = raw_value.strip()
                if candidate:
                    active_path = candidate

        if active_path is None:
            active_path = cfg.path

        if active_path is None:
            _log.warning("Image widget has no 'path' or populated 'source' configured.")
            return

        source = _load_image(active_path)
        if source is None:
            return

        cache_key = (active_path, rect.w, rect.h, cfg.scale.value)
        cached_key = _state.get("scaled_key")
        cached_img = _state.get("scaled_img")
        if cached_key == cache_key and isinstance(cached_img, Image.Image):
            scaled = cached_img
        else:
            scaled = _scale_image(source, rect.w, rect.h, cfg.scale)
            _state["scaled_key"] = cache_key
            _state["scaled_img"] = scaled

        if cfg.scale == ScaleMode.FIT:
            offset_x = rect.x + (rect.w - scaled.width) // 2
            offset_y = rect.y + (rect.h - scaled.height) // 2
            img.paste(scaled, (offset_x, offset_y), scaled)
            return

        img.paste(scaled, (rect.x, rect.y), scaled)
