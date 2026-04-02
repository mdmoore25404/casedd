"""Weather radar widget renderer.

Renders radar imagery from a remote URL when available and falls back to a
brief station status when an image cannot be fetched.
"""

from __future__ import annotations

from io import BytesIO
import logging
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw

from casedd.data_store import DataStore
from casedd.renderer.fonts import fit_font
from casedd.renderer.widgets.base import BaseWidget, content_rect, draw_label, fill_background
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig

_log = logging.getLogger(__name__)
_RADAR_CACHE_TTL_SEC = 300.0
_RADAR_RETRY_BACKOFF_SEC = 30.0


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
        radar_status = str(data.get(f"{root}.radar_status") or "").strip()
        radar_error = str(data.get(f"{root}.radar_error") or "").strip()
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

        indicator_text = self._indicator_text(state, radar_status, radar_error)
        if indicator_text:
            self._draw_indicator(draw, inner, indicator_text)

    def _get_cached_image(
        self,
        state: dict[str, object],
        url: str,
        zoom: float,
    ) -> Image.Image | None:
        """Fetch radar image with simple URL cache and zoom-aware URL candidates."""
        if not url:
            self._clear_fetch_issue(state)
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
        cached_at = state.get("radar_image_fetched_at")
        retry_after = state.get("radar_retry_after")
        now = time.monotonic()
        url_matches = isinstance(cached_url, str) and cached_url == url

        result: Image.Image | None = None

        if (
            url_matches
            and isinstance(cached_image, Image.Image)
            and (
                (isinstance(retry_after, float) and retry_after > now)
                or (
                    isinstance(cached_at, float)
                    and (now - cached_at) < _RADAR_CACHE_TTL_SEC
                )
            )
        ):
            result = cached_image
        else:
            req = Request(url, headers={"User-Agent": "CASEDD/0.2"}, method="GET")  # noqa: S310
            try:
                with urlopen(req, timeout=3) as resp:  # noqa: S310
                    raw = resp.read()
            except HTTPError as exc:
                badge = str(exc.code) if exc.code in {403, 404, 429} else "HTTP"
                reason = (
                    "NWS radar image rate limited (HTTP 429)"
                    if exc.code == 429
                    else f"NWS radar image HTTP {exc.code}"
                )
                self._set_fetch_issue(state, reason, badge)
                if url_matches and isinstance(cached_image, Image.Image):
                    result = cached_image
            except URLError:
                state["radar_retry_after"] = now + _RADAR_RETRY_BACKOFF_SEC
                self._set_fetch_issue(state, "NWS radar image network failure", "NET")
                if url_matches and isinstance(cached_image, Image.Image):
                    result = cached_image
            else:
                try:
                    image = Image.open(BytesIO(raw)).convert("RGB")
                except OSError:
                    state["radar_retry_after"] = now + _RADAR_RETRY_BACKOFF_SEC
                    self._set_fetch_issue(state, "NWS radar image decode failure", "BAD")
                    if url_matches and isinstance(cached_image, Image.Image):
                        result = cached_image
                else:
                    state["radar_image_url"] = url
                    state["radar_image"] = image
                    state["radar_image_fetched_at"] = now
                    state.pop("radar_retry_after", None)
                    self._clear_fetch_issue(state)
                    result = image

        return result

    def _indicator_text(
        self,
        state: dict[str, object],
        radar_status: str,
        radar_error: str,
    ) -> str:
        """Resolve a compact badge for degraded radar states."""
        fetch_badge = state.get("radar_fetch_badge")
        if isinstance(fetch_badge, str) and fetch_badge:
            return fetch_badge

        status = radar_status.strip().lower()
        indicator = ""
        if status == "unavailable":
            indicator = "N/A"
        elif status == "unsupported":
            indicator = "EXT"
        elif status == "unconfigured":
            indicator = "CFG"
        elif status == "error":
            indicator = "META"
        elif status not in {"", "ok"} or radar_error:
            indicator = "RAD"
        return indicator

    def _draw_indicator(
        self,
        draw: ImageDraw.ImageDraw,
        inner: Rect,
        text: str,
    ) -> None:
        """Draw a compact radar status badge in the widget header."""
        font = fit_font(text, max(20, inner.w // 6), 18)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = int(bbox[2] - bbox[0])
        text_h = int(bbox[3] - bbox[1])
        pad_x = 6
        pad_y = 2
        box_w = text_w + (pad_x * 2)
        box_h = text_h + (pad_y * 2)
        box_x = inner.x + inner.w - box_w - 2
        box_y = inner.y + 2
        draw.rounded_rectangle(
            (box_x, box_y, box_x + box_w, box_y + box_h),
            radius=6,
            fill=(68, 24, 24),
            outline=(190, 88, 88),
        )
        draw.text(
            (box_x + pad_x, box_y + pad_y - int(bbox[1])),
            text,
            fill=(255, 230, 230),
            font=font,
        )

    def _set_fetch_issue(self, state: dict[str, object], reason: str, badge: str) -> None:
        """Store and log a radar image fetch problem once per change."""
        previous = state.get("radar_fetch_reason")
        state["radar_fetch_reason"] = reason
        state["radar_fetch_badge"] = badge
        if previous != reason:
            _log.warning("weather radar degraded: %s", reason)

    def _clear_fetch_issue(self, state: dict[str, object]) -> None:
        """Clear a radar image fetch problem after successful recovery."""
        previous = state.get("radar_fetch_reason")
        state.pop("radar_fetch_reason", None)
        state.pop("radar_fetch_badge", None)
        if isinstance(previous, str) and previous:
            _log.info("weather radar recovered")
