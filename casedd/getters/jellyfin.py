"""Jellyfin media server integration.

Polls Jellyfin via its documented REST API and publishes flattened
``jellyfin.*`` keys for server health, active playback sessions, and
library statistics.

Store keys written:
    - ``jellyfin.server.name``
    - ``jellyfin.server.version``
    - ``jellyfin.server.reachable``
    - ``jellyfin.sessions.active_count``
    - ``jellyfin.sessions.transcoding_count``
    - ``jellyfin.sessions.direct_play_count``
    - ``jellyfin.users.active_count``
    - ``jellyfin.library.movies_count``
    - ``jellyfin.library.series_count``
    - ``jellyfin.library.episodes_count``
    - ``jellyfin.library.music_albums_count``
    - ``jellyfin.session_1.user`` ... ``jellyfin.session_N.*``
    - ``jellyfin.session_1.title``
    - ``jellyfin.session_1.media_type``
    - ``jellyfin.session_1.device_name``
    - ``jellyfin.session_1.progress_percent``

Per-session rows are expanded into numbered keys up to ``max_sessions``:
    - ``jellyfin.session_1.*`` ... ``jellyfin.session_N.*``

API Reference: https://api.jellyfin.org/
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import ssl
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)

_MAX_SESSIONS: int = 6
# Ticks are 100-nanosecond intervals; 10 000 000 ticks = 1 second
_TICKS_PER_SECOND: int = 10_000_000


@dataclass(frozen=True)
class _SessionRow:
    """Normalized Jellyfin active playback session."""

    user: str
    title: str
    media_type: str
    device_name: str
    progress_percent: float
    is_transcoding: bool


def _session_progress(session: dict[str, Any]) -> float:
    """Calculate playback progress percentage for a session.

    Args:
        session: Raw session object from the Jellyfin Sessions API.

    Returns:
        Progress as a float in ``[0.0, 100.0]``; ``0.0`` when unavailable.
    """
    play_state = session.get("PlayState", {}) if isinstance(session.get("PlayState"), dict) else {}
    now_playing = (
        session.get("NowPlayingItem", {})
        if isinstance(session.get("NowPlayingItem"), dict)
        else {}
    )
    position = play_state.get("PositionTicks", 0)
    runtime = now_playing.get("RunTimeTicks", 0)
    if runtime and runtime > 0:
        return round(100.0 * max(0, int(position)) / int(runtime), 1)
    return 0.0


def _session_title(session: dict[str, Any]) -> str:
    """Extract the display title from a session's NowPlayingItem.

    For episodes the series name is prepended: ``"Series — Episode Title"``.

    Args:
        session: Raw session object from the Jellyfin Sessions API.

    Returns:
        Human-readable title string.
    """
    item = (
        session.get("NowPlayingItem", {})
        if isinstance(session.get("NowPlayingItem"), dict)
        else {}
    )
    title = str(item.get("Name", ""))
    series = str(item.get("SeriesName", ""))
    if series:
        return f"{series} — {title}"
    return title


def _normalize_sessions(raw_sessions: list[Any], max_sessions: int) -> list[_SessionRow]:
    """Extract active playback sessions from the Jellyfin Sessions list.

    A session is considered active if it has a ``NowPlayingItem`` field.

    Args:
        raw_sessions: Full sessions list from the Jellyfin API.
        max_sessions: Maximum number of rows to return.

    Returns:
        List of up to ``max_sessions`` active session rows.
    """
    rows: list[_SessionRow] = []
    for sess in raw_sessions:
        if not isinstance(sess, dict):
            continue
        if not sess.get("NowPlayingItem"):
            continue
        item = (
            sess.get("NowPlayingItem", {})
            if isinstance(sess.get("NowPlayingItem"), dict)
            else {}
        )
        user = str(sess.get("UserName", ""))
        title = _session_title(sess)
        media_type = str(item.get("Type", ""))
        device = str(sess.get("DeviceName", ""))
        progress = _session_progress(sess)
        is_trans = bool(sess.get("TranscodingInfo"))
        rows.append(
            _SessionRow(
                user=user,
                title=title,
                media_type=media_type,
                device_name=device,
                progress_percent=progress,
                is_transcoding=is_trans,
            )
        )
        if len(rows) >= max_sessions:
            break
    return rows


class JellyfinGetter(BaseGetter):
    """Getter for Jellyfin server health, sessions, and library telemetry.

    Polls the Jellyfin REST API using an admin API key.  When ``base_url`` is
    empty the getter performs no work and returns an empty dict on every cycle
    (disabled / not configured).

    Args:
        store: Shared data store.
        base_url: Jellyfin server base URL (e.g. ``http://localhost:8096``).
            Leave empty to disable the getter entirely.
        api_key: Jellyfin API key (created under Dashboard → API Keys).
        interval: Poll interval in seconds.
        timeout: HTTP request timeout in seconds.
        verify_tls: Verify TLS certificates when using HTTPS.
        max_sessions: Maximum active session rows to flatten into numbered keys.
    """

    def __init__(  # noqa: PLR0913 -- explicit config wiring is clearer
        self,
        store: DataStore,
        base_url: str = "",
        api_key: str | None = None,
        interval: float = 5.0,
        timeout: float = 4.0,
        verify_tls: bool = True,
        max_sessions: int = _MAX_SESSIONS,
    ) -> None:
        """Initialise the JellyfinGetter."""
        super().__init__(store, interval)
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key.strip() if isinstance(api_key, str) else ""
        self._timeout = timeout
        self._max_sessions = max(1, max_sessions)
        self._ssl_context: ssl.SSLContext | None = None
        if self._base_url.startswith("https://") and not verify_tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self._ssl_context = ctx

    def _auth_headers(self) -> dict[str, str]:
        """Build Jellyfin authentication headers.

        Returns:
            Dict containing the ``X-Emby-Token`` header when an API key is
            configured; empty dict otherwise.
        """
        if self._api_key:
            return {"X-Emby-Token": self._api_key}
        return {}

    def _get_json(self, path: str) -> Any:
        """Perform a synchronous authenticated GET and parse JSON.

        Args:
            path: URL path relative to the base URL (must start with ``/``).

        Returns:
            Parsed JSON body (list, dict, or primitive).

        Raises:
            RuntimeError: On HTTP 401/403, any other HTTP error, or network
                failure.
        """
        url = f"{self._base_url}{path}"
        headers = self._auth_headers()
        req = Request(url, headers=headers, method="GET")  # noqa: S310 -- user-provided URL
        try:
            with urlopen(  # noqa: S310 -- user-provided URL
                req,
                timeout=self._timeout,
                context=self._ssl_context,
            ) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise RuntimeError("Jellyfin auth failed — check API key") from exc
            raise RuntimeError(f"Jellyfin HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"Jellyfin transport error: {exc}") from exc

    async def fetch(self) -> dict[str, StoreValue]:
        """Poll Jellyfin and return flattened store updates.

        Fetches server info, sessions, and library counts in parallel.

        Returns:
            Mapping of ``jellyfin.*`` store keys to current values, or an
            empty dict when the getter is not configured.
        """
        if not self._base_url:
            return {}

        info_raw, sessions_raw, counts_raw = await asyncio.gather(
            asyncio.to_thread(self._get_json, "/System/Info"),
            asyncio.to_thread(self._get_json, "/Sessions"),
            asyncio.to_thread(self._get_counts_safe),
        )

        updates: dict[str, StoreValue] = {}
        updates.update(self._parse_server_info(info_raw))
        updates.update(self._parse_sessions(sessions_raw))
        updates.update(self._parse_counts(counts_raw))
        return updates

    def _get_counts_safe(self) -> dict[str, Any]:
        """Fetch library counts, returning an empty dict on failure.

        Library counts may not be accessible with all API key scopes.
        Failures here should not block server info or session data.

        Returns:
            Raw counts dict from ``/Items/Counts``, or ``{}`` on error.
        """
        try:
            result = self._get_json("/Items/Counts")
            return result if isinstance(result, dict) else {}
        except RuntimeError:
            return {}

    def _parse_server_info(self, raw: Any) -> dict[str, StoreValue]:
        """Extract server identity from a ``/System/Info`` response.

        Args:
            raw: Parsed JSON from the Jellyfin System/Info endpoint.

        Returns:
            Mapping of ``jellyfin.server.*`` store keys.
        """
        info = raw if isinstance(raw, dict) else {}
        return {
            "jellyfin.server.name": str(info.get("ServerName", "")),
            "jellyfin.server.version": str(info.get("Version", "")),
            "jellyfin.server.reachable": 1,
        }

    def _parse_sessions(self, raw: Any) -> dict[str, StoreValue]:
        """Extract session metrics from a ``/Sessions`` response.

        Args:
            raw: Parsed JSON list from the Jellyfin Sessions endpoint.

        Returns:
            Mapping of ``jellyfin.sessions.*`` and numbered
            ``jellyfin.session_N.*`` store keys.
        """
        all_sessions: list[Any] = raw if isinstance(raw, list) else []
        active = _normalize_sessions(all_sessions, self._max_sessions)

        transcoding_count = sum(1 for s in active if s.is_transcoding)
        direct_play_count = sum(1 for s in active if not s.is_transcoding)

        # Count unique active users across all sessions (active or idle)
        active_users: set[str] = set()
        for sess in all_sessions:
            if isinstance(sess, dict) and sess.get("NowPlayingItem") and sess.get("UserName"):
                active_users.add(str(sess["UserName"]))

        updates: dict[str, StoreValue] = {
            "jellyfin.sessions.active_count": len(active),
            "jellyfin.sessions.transcoding_count": transcoding_count,
            "jellyfin.sessions.direct_play_count": direct_play_count,
            "jellyfin.users.active_count": len(active_users),
        }

        # Always write all session slots so stale rows clear when sessions end.
        for idx in range(1, self._max_sessions + 1):
            pfx = f"jellyfin.session_{idx}"
            updates[f"{pfx}.user"] = ""
            updates[f"{pfx}.title"] = ""
            updates[f"{pfx}.media_type"] = ""
            updates[f"{pfx}.device_name"] = ""
            updates[f"{pfx}.progress_percent"] = 0.0

        for idx, sess in enumerate(active, start=1):
            pfx = f"jellyfin.session_{idx}"
            updates[f"{pfx}.user"] = sess.user
            updates[f"{pfx}.title"] = sess.title
            updates[f"{pfx}.media_type"] = sess.media_type
            updates[f"{pfx}.device_name"] = sess.device_name
            updates[f"{pfx}.progress_percent"] = sess.progress_percent

        return updates

    def _parse_counts(self, raw: dict[str, Any]) -> dict[str, StoreValue]:
        """Extract library counts from an ``/Items/Counts`` response.

        Args:
            raw: Parsed JSON dict from the Jellyfin Items/Counts endpoint.
                May be empty when the endpoint was unavailable.

        Returns:
            Mapping of ``jellyfin.library.*`` store keys.
        """
        return {
            "jellyfin.library.movies_count": int(raw.get("MovieCount", 0)),
            "jellyfin.library.series_count": int(raw.get("SeriesCount", 0)),
            "jellyfin.library.episodes_count": int(raw.get("EpisodeCount", 0)),
            "jellyfin.library.music_albums_count": int(raw.get("AlbumCount", 0)),
        }
