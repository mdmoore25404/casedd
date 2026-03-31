"""NASA Astronomy Picture of the Day (APOD) getter.

Fetches today's APOD metadata and image from the NASA APOD API and caches
both locally.  Because the image changes at most once per day the getter
polls hourly by default; it skips the network round-trip when the cached
date already matches today's date.

API reference: https://api.nasa.gov/ (endpoint: /planetary/apod)

Requires a NASA API key configured via ``CASEDD_NASA_API_KEY`` (or the
``nasa_api_key`` YAML field).  Without a key the public demo key
``DEMO_KEY`` is used, which is limited to 30 requests/hour/IP.

Store keys written:
    - ``apod.available``   (float) -- 1.0 when an image is cached, 0.0 otherwise
    - ``apod.date``        (str)   -- YYYY-MM-DD date of the current APOD
    - ``apod.title``       (str)   -- title of the APOD
    - ``apod.copyright``   (str)   -- attribution string (empty if not present)
    - ``apod.explanation`` (str)   -- full text explanation (may be long)
    - ``apod.media_type``  (str)   -- ``"image"`` or ``"video"``
    - ``apod.image_path``  (str)   -- local filesystem path to the cached image
"""

from __future__ import annotations

import asyncio
from datetime import UTC
from datetime import datetime as _datetime
import json
import logging
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)

_APOD_URL = "https://api.nasa.gov/planetary/apod"
# Fallback key rate-limited to 30 req/hour/IP — acceptable for daily polling.
_DEMO_KEY = "DEMO_KEY"
_TIMEOUT = 20  # seconds for HTTP calls


class ApodGetter(BaseGetter):
    """Getter for the NASA Astronomy Picture of the Day.

    Args:
        store: Shared data store instance.
        api_key: NASA API key (uses public demo key if omitted).
        interval: Poll interval in seconds.  Defaults to 3600 (hourly) since
            the image changes at most once per day.
        cache_dir: Directory to store downloaded images.  Created on first use.
    """

    def __init__(
        self,
        store: DataStore,
        api_key: str | None = None,
        interval: float = 3600.0,
        cache_dir: str = "assets/apod",
    ) -> None:
        """Initialise the APOD getter.

        Args:
            store: The shared :class:`~casedd.data_store.DataStore`.
            api_key: NASA API key.  Falls back to DEMO_KEY.
            interval: Seconds between polls (default: 3600).
            cache_dir: Local directory for caching downloaded images.
        """
        super().__init__(store, interval)
        self._api_key = api_key or _DEMO_KEY
        self._cache_dir = Path(cache_dir)
        # Track the last successfully fetched date to skip redundant downloads.
        self._cached_date: str = ""

    async def fetch(self) -> dict[str, StoreValue]:
        """Fetch APOD metadata and download image if needed.

        Returns:
            Dict of store key/value pairs to write.
        """
        return await asyncio.to_thread(self._sample)

    def _sample(self) -> dict[str, StoreValue]:
        """Blocking APOD fetch implementation.

        Returns:
            Dict of store key/value pairs to write.
        """
        today = _datetime.now(tz=UTC).date().isoformat()
        if self._cached_date == today:
            # Image already cached for today; nothing to do.
            return {}

        metadata = self._fetch_metadata()
        if metadata is None:
            cached = self._latest_cached_image()
            if cached is not None:
                _log.info("APOD metadata fetch failed; using cached image %s", cached)
                return {
                    "apod.available": 1.0,
                    "apod.image_path": str(cached),
                }
            return {"apod.available": 0.0}

        media_type = str(metadata.get("media_type", ""))
        apod_date = str(metadata.get("date", today))
        title = str(metadata.get("title", ""))
        copyright_text = str(metadata.get("copyright", "")).strip()
        explanation = str(metadata.get("explanation", ""))

        result: dict[str, StoreValue] = {
            "apod.date": apod_date,
            "apod.title": title,
            "apod.copyright": copyright_text,
            "apod.explanation": explanation,
            "apod.media_type": media_type,
            "apod.available": 0.0,
            "apod.image_path": "",
        }

        image_url = ""
        if media_type == "image":
            # Prefer the HD URL; fall back to the standard URL.
            image_url = str(metadata.get("hdurl") or metadata.get("url", ""))
        else:
            _log.info("APOD for %s is a %s (not an image); skipping.", apod_date, media_type)

        image_path = self._download_image(image_url, apod_date) if image_url else None
        if image_path is not None:
            self._cached_date = apod_date
            result["apod.available"] = 1.0
            result["apod.image_path"] = str(image_path)
            _log.info("APOD fetched: '%s' (%s) → %s", title, apod_date, image_path)
        elif media_type == "image" and not image_url:
            _log.warning("APOD metadata has no image URL for %s", apod_date)

        return result

    def _fetch_metadata(self) -> dict[str, object] | None:
        """Call the NASA APOD API and return parsed JSON metadata.

        Returns:
            Parsed response dict, or ``None`` on error.
        """
        params = urlencode({"api_key": self._api_key})
        url = f"{_APOD_URL}?{params}"
        req = Request(url, headers={"User-Agent": "CASEDD/0.2"})  # noqa: S310 -- fixed HTTPS URL
        try:
            with urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310 -- fixed HTTPS URL, not user input
                body = resp.read()
        except (TimeoutError, URLError) as exc:
            _log.warning("APOD API request failed: %s", exc)
            return None

        try:
            return dict(json.loads(body))
        except (json.JSONDecodeError, TypeError) as exc:
            _log.warning("APOD API returned invalid JSON: %s", exc)
            return None

    def _download_image(self, url: str, apod_date: str) -> Path | None:
        """Download an APOD image to the local cache directory.

        Args:
            url: HTTPS URL of the image to download.
            apod_date: YYYY-MM-DD date string used to name the cached file.

        Returns:
            Path to the cached file, or ``None`` on failure.
        """
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        # Derive extension from the URL; default to .jpg if absent.
        url_path = url.split("?", maxsplit=1)[0]
        suffix = Path(url_path).suffix or ".jpg"
        dest = self._cache_dir / f"apod_{apod_date}{suffix}"

        if dest.is_file():
            _log.debug("APOD image already cached at %s", dest)
            return dest

        req = Request(url, headers={"User-Agent": "CASEDD/0.2"})  # noqa: S310 -- HTTPS URL from trusted API
        try:
            with urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310 -- HTTPS URL from trusted API
                data = resp.read()
        except (TimeoutError, URLError) as exc:
            _log.warning("Failed to download APOD image from %s: %s", url, exc)
            return None

        try:
            dest.write_bytes(data)
        except OSError as exc:
            _log.warning("Failed to write APOD image to %s: %s", dest, exc)
            return None

        return dest

    def _latest_cached_image(self) -> Path | None:
        """Return the most recent cached APOD image path, if one exists."""
        if not self._cache_dir.is_dir():
            return None

        candidates = [
            path
            for path in self._cache_dir.glob("apod_*.*")
            if path.is_file()
        ]
        if not candidates:
            return None

        return max(candidates, key=lambda path: path.stem)
