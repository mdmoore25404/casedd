"""Plex server getter.

Polls a Plex Media Server over HTTP(S) with token authentication and publishes
flattened ``plex.*`` keys for dashboard widgets.

Store keys written:
    - ``plex.server.name``
    - ``plex.server.version``
    - ``plex.server.platform``
    - ``plex.server.reachable``
    - ``plex.sessions.active_count``
    - ``plex.sessions.transcoding_count``
    - ``plex.sessions.direct_play_count``
    - ``plex.sessions.direct_stream_count``
    - ``plex.bandwidth.current_mbps``
    - ``plex.sessions.rows``
    - ``plex.recently_added.count``
    - ``plex.recently_added.rows``
    - ``plex.summary``

Per-session and per-item rows are also expanded into numbered keys:
    - ``plex.session_1.*`` ... ``plex.session_N.*``
    - ``plex.recently_added_1.*`` ... ``plex.recently_added_N.*``
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _SessionRow:
    """Normalized active Plex session row."""

    user: str
    title: str
    media_type: str
    progress_percent: float
    transcode_decision: str
    bitrate_kbps: float
    library: str


@dataclass(frozen=True)
class _RecentItem:
    """Normalized recently added library item row."""

    title: str
    media_type: str
    library: str
    added_at: str


class PlexGetter(BaseGetter):
    """Getter for Plex server/session/library telemetry.

    Args:
        store: Shared data store.
        base_url: Plex server base URL.
        token: Plex token used for API authentication.
        client_identifier: X-Plex-Client-Identifier header value.
        product: X-Plex-Product header value.
        interval: Poll interval in seconds.
        timeout: HTTP timeout in seconds.
        verify_tls: Verify TLS certificates when using HTTPS.
        max_sessions: Maximum active session rows to flatten.
        max_recent: Maximum recently-added rows to flatten.
        privacy_filter_regex: Optional regex used to redact titles and
            library names for privacy-sensitive displays.
        privacy_filter_libraries: Optional list of specific library names
            that should always be redacted.
        privacy_redaction_text: Replacement text for redacted values.
    """

    def __init__(  # noqa: PLR0913 -- explicit config wiring is clearer
        self,
        store: DataStore,
        base_url: str = "http://localhost:32400",
        token: str | None = None,
        client_identifier: str = "casedd",
        product: str = "CASEDD",
        interval: float = 5.0,
        timeout: float = 4.0,
        verify_tls: bool = True,
        max_sessions: int = 6,
        max_recent: int = 6,
        privacy_filter_regex: str | None = None,
        privacy_filter_libraries: list[str] | None = None,
        privacy_redaction_text: str = "[hidden]",
    ) -> None:
        """Initialise Plex getter."""
        super().__init__(store, interval)
        self._base_url = base_url.rstrip("/")
        self._token = token.strip() if isinstance(token, str) else ""
        self._client_identifier = client_identifier.strip() or "casedd"
        self._product = product.strip() or "CASEDD"
        self._timeout = timeout
        self._max_sessions = max(1, max_sessions)
        self._max_recent = max(1, max_recent)
        self._privacy_redaction_text = privacy_redaction_text.strip() or "[hidden]"
        self._privacy_rx = _compile_privacy_regex(privacy_filter_regex)
        self._privacy_libraries = _normalize_library_names(privacy_filter_libraries)
        self._ssl_context: ssl.SSLContext | None = None
        if self._base_url.startswith("https://") and not verify_tls:
            self._ssl_context = ssl._create_unverified_context()  # noqa: S323  # explicit opt-out for self-signed local Plex

    async def fetch(self) -> dict[str, StoreValue]:
        """Collect one Plex sample."""
        return await asyncio.to_thread(self._sample)

    def _sample(self) -> dict[str, StoreValue]:
        """Blocking Plex poll implementation."""
        identity = self._request_xml("/")
        sessions_xml = self._request_xml("/status/sessions")
        sections_xml = self._request_xml("/library/sections")
        recent_xml = self._request_xml(
            "/library/recentlyAdded",
            query={
                "X-Plex-Container-Start": "0",
                "X-Plex-Container-Size": str(self._max_recent),
            },
        )

        sessions = _parse_sessions(sessions_xml)
        recent = _parse_recently_added(recent_xml)
        movies, shows, albums = self._collect_library_counts(sections_xml)

        sanitized_sessions = [self._sanitize_session(item) for item in sessions]
        sanitized_recent = [self._sanitize_recent_item(item) for item in recent]

        transcoding_count = sum(
            1 for item in sanitized_sessions if item.transcode_decision == "transcode"
        )
        direct_stream_count = sum(
            1 for item in sanitized_sessions if item.transcode_decision == "direct_stream"
        )
        direct_play_count = sum(
            1 for item in sanitized_sessions if item.transcode_decision == "direct_play"
        )
        bandwidth_mbps = sum(item.bitrate_kbps for item in sanitized_sessions) / 1000.0

        payload: dict[str, StoreValue] = {
            "plex.server.name": identity.get("friendlyName", "Plex"),
            "plex.server.version": identity.get("version", ""),
            "plex.server.platform": identity.get("platform", ""),
            "plex.server.reachable": 1,
            "plex.sessions.active_count": float(len(sanitized_sessions)),
            "plex.sessions.transcoding_count": float(transcoding_count),
            "plex.sessions.direct_play_count": float(direct_play_count),
            "plex.sessions.direct_stream_count": float(direct_stream_count),
            "plex.bandwidth.current_mbps": round(max(0.0, bandwidth_mbps), 2),
            "plex.library.movies_count": float(movies),
            "plex.library.shows_count": float(shows),
            "plex.library.music_albums_count": float(albums),
            "plex.sessions.rows": _render_session_rows(sanitized_sessions),
            "plex.recently_added.count": float(len(sanitized_recent)),
            "plex.recently_added.rows": _render_recent_rows(sanitized_recent),
            "plex.summary": (
                f"{len(sanitized_sessions)} active / {transcoding_count} transcode"
            ),
        }
        payload.update(_expand_session_keys(sanitized_sessions, self._max_sessions))
        payload.update(_expand_recent_keys(sanitized_recent, self._max_recent))
        return payload

    def _collect_library_counts(self, sections_xml: ET.Element) -> tuple[int, int, int]:
        """Fetch aggregate counts from movie/show/music sections."""
        movies_count = 0
        shows_count = 0
        albums_count = 0

        for section in sections_xml.findall("Directory"):
            section_key = section.get("key", "").strip()
            section_type = section.get("type", "").strip()
            if not section_key or section_type not in {"movie", "show", "artist"}:
                continue

            count = self._fetch_section_total(section_key)
            if section_type == "movie":
                movies_count += count
            elif section_type == "show":
                shows_count += count
            else:
                albums_count += count

        return movies_count, shows_count, albums_count

    def _fetch_section_total(self, section_key: str) -> int:
        """Read the total item count for one Plex library section."""
        section_xml = self._request_xml(
            f"/library/sections/{section_key}/all",
            query={"X-Plex-Container-Start": "0", "X-Plex-Container-Size": "0"},
        )
        total_raw = section_xml.get("totalSize", "0")
        return int(_to_float(total_raw))

    def _request_xml(
        self,
        path: str,
        query: dict[str, str] | None = None,
    ) -> ET.Element:
        """Perform one Plex API request and parse XML payload."""
        url = f"{self._base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"

        headers = {
            "Accept": "application/xml",
            "X-Plex-Client-Identifier": self._client_identifier,
            "X-Plex-Product": self._product,
        }
        if self._token:
            headers["X-Plex-Token"] = self._token

        req = Request(url, headers=headers, method="GET")  # noqa: S310 -- user-provided Plex endpoint

        try:
            with urlopen(  # noqa: S310 -- user-provided Plex endpoint
                req,
                timeout=self._timeout,
                context=self._ssl_context,
            ) as resp:
                body = resp.read()
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise RuntimeError("Plex auth failed (check token permissions)") from exc
            raise RuntimeError(f"Plex request failed with HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"Plex transport error: {exc}") from exc

        try:
            return ET.fromstring(body)  # noqa: S314  # trusted local Plex API payload
        except ET.ParseError as exc:
            raise RuntimeError(f"Plex XML parse error at {path}: {exc}") from exc

    def _sanitize_session(self, item: _SessionRow) -> _SessionRow:
        """Apply privacy redaction to one session row."""
        redact_library = self._library_is_private(item.library)
        redacted_user = self._redact(item.user)
        redacted_title = self._redact(item.title, redact_library=redact_library)
        return _SessionRow(
            user=redacted_user,
            title=redacted_title,
            media_type=item.media_type,
            progress_percent=item.progress_percent,
            transcode_decision=item.transcode_decision,
            bitrate_kbps=item.bitrate_kbps,
            library=self._redact(item.library, redact_library=redact_library),
        )

    def _sanitize_recent_item(self, item: _RecentItem) -> _RecentItem:
        """Apply privacy redaction to one recently-added row."""
        redact_library = self._library_is_private(item.library)
        return _RecentItem(
            title=self._redact(item.title, redact_library=redact_library),
            media_type=item.media_type,
            library=self._redact(item.library, redact_library=redact_library),
            added_at=item.added_at,
        )

    def _redact(self, value: str, *, redact_library: bool = False) -> str:
        """Redact values that match the configured privacy regex."""
        if redact_library:
            return self._privacy_redaction_text
        if self._privacy_rx is None:
            return value
        if self._privacy_rx.search(value):
            return self._privacy_redaction_text
        return value

    def _library_is_private(self, library_name: str) -> bool:
        """Return whether *library_name* is configured for explicit redaction."""
        return library_name.strip().lower() in self._privacy_libraries


def _compile_privacy_regex(pattern: str | None) -> re.Pattern[str] | None:
    """Compile privacy regex and ignore invalid patterns."""
    if pattern is None:
        return None
    text = pattern.strip()
    if not text:
        return None
    try:
        return re.compile(text, flags=re.IGNORECASE)
    except re.error:
        _log.warning("Invalid CASEDD_PLEX_PRIVACY_FILTER_REGEX; privacy filter disabled")
        return None


def _normalize_library_names(names: list[str] | None) -> set[str]:
    """Normalize configured library names for case-insensitive matching."""
    if names is None:
        return set()
    return {name.strip().lower() for name in names if name.strip()}


def _parse_sessions(root: ET.Element) -> list[_SessionRow]:
    """Parse active sessions from /status/sessions XML."""
    rows: list[_SessionRow] = []
    for item in root.findall("Video") + root.findall("Track"):
        user = _session_user(item)
        title = _session_title(item)
        media_type = item.tag.lower()
        progress_percent = _session_progress(item)
        decision = _transcode_decision(item)
        bitrate_kbps = _to_float(item.get("bitrate", "0"))
        library = str(item.get("librarySectionTitle") or "")
        rows.append(
            _SessionRow(
                user=user,
                title=title,
                media_type=media_type,
                progress_percent=progress_percent,
                transcode_decision=decision,
                bitrate_kbps=bitrate_kbps,
                library=library,
            )
        )
    return rows


def _parse_recently_added(root: ET.Element) -> list[_RecentItem]:
    """Parse recently added rows from /library/recentlyAdded XML."""
    items: list[_RecentItem] = []
    for item in root.findall("Video") + root.findall("Directory") + root.findall("Track"):
        media_type = item.get("type", item.tag).strip().lower() or "unknown"
        title = _recent_title(item)
        library = str(item.get("librarySectionTitle") or "")
        added_at = str(item.get("addedAt") or "")
        items.append(
            _RecentItem(
                title=title,
                media_type=media_type,
                library=library,
                added_at=added_at,
            )
        )
    return items


def _session_user(item: ET.Element) -> str:
    """Extract user name from a session item."""
    user_node = item.find("User")
    if user_node is not None:
        title = user_node.get("title", "").strip()
        if title:
            return title
    return "Unknown"


def _session_title(item: ET.Element) -> str:
    """Extract title from a session item."""
    raw = item.get("grandparentTitle") or item.get("title") or "Untitled"
    return str(raw).strip() or "Untitled"


def _recent_title(item: ET.Element) -> str:
    """Extract title for recently-added items."""
    for key in ("title", "grandparentTitle", "parentTitle"):
        raw = item.get(key)
        if raw is not None and raw.strip():
            return raw.strip()
    return "Untitled"


def _session_progress(item: ET.Element) -> float:
    """Compute progress percentage from viewOffset and duration."""
    offset = _to_float(item.get("viewOffset", "0"))
    duration = _to_float(item.get("duration", "0"))
    if duration <= 0.0:
        return 0.0
    return max(0.0, min(100.0, (offset / duration) * 100.0))


def _transcode_decision(item: ET.Element) -> str:
    """Normalize Plex play mode into direct_play/direct_stream/transcode."""
    if item.find("TranscodeSession") is not None:
        return "transcode"

    decision = "direct_play"
    media = item.find("Media")
    part = media.find("Part") if media is not None else None
    stream_nodes = part.findall("Stream") if part is not None else []

    has_transcode = False
    has_copy = False
    for stream in stream_nodes:
        stream_decision = (stream.get("decision") or "").strip().lower()
        if stream_decision == "transcode":
            has_transcode = True
        elif stream_decision == "copy":
            has_copy = True

    if has_transcode:
        decision = "transcode"
    elif has_copy:
        decision = "direct_stream"

    return decision


def _render_session_rows(rows: list[_SessionRow]) -> str:
    """Render session rows for table widgets."""
    return "\n".join(
        (
            f"{row.user}|{row.title}|{row.media_type}|"
            f"{row.progress_percent:.1f}|{row.transcode_decision}"
        )
        for row in rows
    )


def _render_recent_rows(rows: list[_RecentItem]) -> str:
    """Render recently-added rows for table widgets."""
    return "\n".join(f"{row.media_type}|{row.library}|{row.title}" for row in rows)


def _expand_session_keys(
    rows: list[_SessionRow],
    max_rows: int,
) -> dict[str, StoreValue]:
    """Flatten per-session values into numbered keys."""
    payload: dict[str, StoreValue] = {}
    for idx in range(max_rows):
        row_index = idx + 1
        prefix = f"plex.session_{row_index}"
        if idx < len(rows):
            row = rows[idx]
            payload[f"{prefix}.user"] = row.user
            payload[f"{prefix}.title"] = row.title
            payload[f"{prefix}.media_type"] = row.media_type
            payload[f"{prefix}.progress_percent"] = round(row.progress_percent, 1)
            payload[f"{prefix}.transcode_decision"] = row.transcode_decision
            payload[f"{prefix}.library"] = row.library
        else:
            payload[f"{prefix}.user"] = ""
            payload[f"{prefix}.title"] = ""
            payload[f"{prefix}.media_type"] = ""
            payload[f"{prefix}.progress_percent"] = 0.0
            payload[f"{prefix}.transcode_decision"] = ""
            payload[f"{prefix}.library"] = ""
    return payload


def _expand_recent_keys(
    rows: list[_RecentItem],
    max_rows: int,
) -> dict[str, StoreValue]:
    """Flatten recently-added values into numbered keys."""
    payload: dict[str, StoreValue] = {}
    for idx in range(max_rows):
        row_index = idx + 1
        prefix = f"plex.recently_added_{row_index}"
        if idx < len(rows):
            row = rows[idx]
            payload[f"{prefix}.title"] = row.title
            payload[f"{prefix}.media_type"] = row.media_type
            payload[f"{prefix}.library"] = row.library
            payload[f"{prefix}.added_at"] = row.added_at
        else:
            payload[f"{prefix}.title"] = ""
            payload[f"{prefix}.media_type"] = ""
            payload[f"{prefix}.library"] = ""
            payload[f"{prefix}.added_at"] = ""
    return payload


def _to_float(raw: str | float | int) -> float:
    """Convert mixed numeric input to float with safe fallback."""
    if isinstance(raw, int | float):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0
