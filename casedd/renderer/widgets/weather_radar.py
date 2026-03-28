"""Weather radar widget renderer.

Renders radar imagery from a remote URL when available and falls back to URL
and station text when an image cannot be fetched.
"""

from __future__ import annotations

from io import BytesIO
from urllib.error import URLError
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw

from casedd.data_store import DataStore
from casedd.renderer.color import parse_color
from casedd.renderer.fonts import fit_font, get_font
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
        radar_url = str(data.get(f"{root}.radar_url") or "").strip()
        radar_station = str(data.get(f"{root}.radar_station") or "").strip()

        image_rect = Rect(inner.x + 2, inner.y + label_h + 2, inner.w - 4, inner.h - label_h - 22)
        image = self._get_cached_image(state, radar_image_url)
        if image is not None and image_rect.w > 10 and image_rect.h > 10:
            fitted = image.copy()
            fitted.thumbnail((image_rect.w, image_rect.h), Image.Resampling.LANCZOS)
            x = image_rect.x + (image_rect.w - fitted.width) // 2
            y = image_rect.y + (image_rect.h - fitted.height) // 2
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

        footer = radar_url if radar_url else "No radar URL"
        footer_font = get_font(max(10, inner.h // 20))
        footer_color = parse_color(cfg.color, fallback=(120, 182, 220))
        draw.text(
            (inner.x + 4, inner.y + inner.h - 14),
            footer[:90],
            fill=footer_color,
            font=footer_font,
        )

    def _get_cached_image(self, state: dict[str, object], url: str) -> Image.Image | None:
        """Fetch radar image with simple URL cache."""
        if not url:
            return None

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
