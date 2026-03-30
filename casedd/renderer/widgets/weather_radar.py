"""Weather radar widget renderer.

Renders radar imagery from a remote URL when available and falls back to a
brief station status when an image cannot be fetched.
"""

from __future__ import annotations

from io import BytesIO
from urllib.error import URLError
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw

from casedd.data_store import DataStore
from casedd.renderer.fonts import fit_font
from casedd.renderer.widgets.base import BaseWidget, content_rect, draw_label, fill_background
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig


class WeatherRadarWidget(BaseWidget):
    """Render weather radar image/status details."""

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        state: dict[str, object],
    ) -> None:
        """Paint weather radar widget."""
        fill_background(img, rect, cfg.background)
        inner = content_rect(rect, cfg.padding)
        draw = ImageDraw.Draw(img)

        title = cfg.label if cfg.label else "Radar"
        label_h = draw_label(draw, inner, title, color=(150, 150, 150))

        prefix = cfg.source.strip() if cfg.source else "weather.radar_url"
        if prefix.endswith(".radar_url"):
            root = prefix[: -len(".radar_url")]
        elif prefix.endswith("."):
            root = prefix[:-1]
        else:
            root = prefix

        radar_image_url = str(data.get(f"{root}.radar_image_url") or "").strip()
        radar_station = str(data.get(f"{root}.radar_station") or "").strip()
        zoom = cfg.zoom if cfg.zoom >= 1.0 else 1.0

        image_rect = Rect(inner.x + 2, inner.y + label_h + 2, inner.w - 4, inner.h - label_h - 6)
        image = self._get_cached_image(state, radar_image_url, zoom)
        if image is not None and image_rect.w > 10 and image_rect.h > 10:
            src_w, src_h = image.size
            if src_w > 0 and src_h > 0:
                scale = min(image_rect.w / src_w, image_rect.h / src_h)
                out_w = max(1, int(src_w * scale))
                out_h = max(1, int(src_h * scale))
                fitted = image.resize((out_w, out_h), Image.Resampling.LANCZOS)
                x = image_rect.x + (image_rect.w - out_w) // 2
                y = image_rect.y + (image_rect.h - out_h) // 2
                img.paste(fitted.convert("RGB"), (x, y))
        else:
            fallback = radar_station if radar_station else "Radar image unavailable"
            font = fit_font(fallback, max(20, image_rect.w - 8), max(16, image_rect.h - 8))
            bbox = font.getbbox(fallback)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            tx = image_rect.x + max(2, (image_rect.w - tw) // 2)
            ty = image_rect.y + max(2, (image_rect.h - th) // 2)
            draw.text((tx, ty), fallback, fill=(195, 205, 215), font=font)

    def _get_cached_image(
        self,
        state: dict[str, object],
        url: str,
        zoom: float,
    ) -> Image.Image | None:
        """Fetch radar image with simple URL cache and zoom-aware URL candidates."""
        if not url:
            return None

        for candidate in self._zoom_candidates(url, zoom):
            image = self._fetch_cached_by_url(state, candidate)
            if image is not None:
                return image
        return None

    def _zoom_candidates(self, url: str, zoom: float) -> list[str]:
        """Generate likely NWS radar image variants for requested zoom levels."""
        candidates = [url]
        if "/ridge/standard/" not in url:
            return candidates

        if zoom >= 2.5:
            candidates.append(url.replace("/ridge/standard/", "/ridge/lite/"))
            candidates.append(url.replace("/ridge/standard/", "/ridge/small/"))
        elif zoom >= 1.5:
            candidates.append(url.replace("/ridge/standard/", "/ridge/lite/"))
        return candidates

    def _fetch_cached_by_url(self, state: dict[str, object], url: str) -> Image.Image | None:
        """Fetch and cache one URL.

        Args:
            state: Widget-local state map.
            url: Candidate image URL.

        Returns:
            Loaded image when successful, otherwise None.
        """

        cached_url = state.get("radar_image_url")
        cached_image = state.get("radar_image")
        if (
            isinstance(cached_url, str)
            and cached_url == url
            and isinstance(cached_image, Image.Image)
        ):
            return cached_image

        req = Request(url, headers={"User-Agent": "CASEDD/0.2"}, method="GET")  # noqa: S310
        try:
            with urlopen(req, timeout=3) as resp:  # noqa: S310
                raw = resp.read()
        except URLError:
            return None

        try:
            image = Image.open(BytesIO(raw)).convert("RGB")
        except OSError:
            return None

        state["radar_image_url"] = url
        state["radar_image"] = image
        return image
