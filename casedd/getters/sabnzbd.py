"""SABnzbd downloader integration.

Polls SABnzbd via its documented HTTP API and publishes flattened
``sabnzbd.*`` keys for queue tracking, status, and history.

Store keys written:
    - ``sabnzbd.version``
    - ``sabnzbd.status.paused``
    - ``sabnzbd.queue.total``
    - ``sabnzbd.queue.active_count``
    - ``sabnzbd.queue.remaining_mb``
    - ``sabnzbd.queue.remaining_size``  — human-readable size string (MB/GB/TB)
    - ``sabnzbd.rate.mbps``
    - ``sabnzbd.eta_seconds``
    - ``sabnzbd.eta_hms``  — ETA formatted as ``HH:MM:SS``
    - ``sabnzbd.disk.free_gb``
    - ``sabnzbd.history.success_count``
    - ``sabnzbd.history.failed_count``
    - ``sabnzbd.slot_1.name`` ... ``sabnzbd.slot_N.*``
    - ``sabnzbd.slot_1.category``
    - ``sabnzbd.slot_1.progress_percent``
    - ``sabnzbd.slot_1.timeleft_seconds``

Per-slot rows are expanded into numbered keys up to ``max_slots``:
    - ``sabnzbd.slot_1.*`` ... ``sabnzbd.slot_N.*``

API Reference: https://sabnzbd.org/wiki/advanced/api
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import ssl
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)

_MAX_SLOTS: int = 6
_HISTORY_LIMIT: int = 100


def _parse_speed_mbps(speed_str: str) -> float:  # noqa: PLR0911 -- unit dispatch needs multiple returns
    """Parse a SABnzbd speed string to MB/s.

    SABnzbd returns speed as ``"<value> <unit>"`` where unit is one of
    ``B``, ``K``, ``M``, or ``G`` (bytes/s, KB/s, MB/s, GB/s).

    Args:
        speed_str: Raw speed string from the SABnzbd queue response.

    Returns:
        Speed in MB/s as a float; ``0.0`` on any parse failure.
    """
    parts = speed_str.strip().split()
    if not parts:
        return 0.0
    try:
        value = float(parts[0])
    except ValueError:
        return 0.0
    if len(parts) < 2:
        # Bare numeric byte value
        return value / 1024.0 / 1024.0
    unit = parts[1].upper()
    if unit in ("G", "GB"):
        return value * 1024.0
    if unit in ("M", "MB"):
        return value
    if unit in ("K", "KB"):
        return value / 1024.0
    # Bare bytes or unknown unit — treat as bytes/s
    return value / 1024.0 / 1024.0


def _parse_timeleft_seconds(timeleft: str) -> int:
    """Parse a SABnzbd timeleft string to total seconds.

    Accepts ``"H:MM:SS"`` and ``"H:MM"`` formats.

    Args:
        timeleft: Time-left string from the SABnzbd API.

    Returns:
        Duration in whole seconds; ``0`` on parse failure.
    """
    parts = timeleft.strip().split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        pass
    return 0


def _seconds_to_hms(seconds: int) -> str:
    """Format a duration in seconds as ``HH:MM:SS``.

    Args:
        seconds: Duration in whole seconds.

    Returns:
        Formatted string like ``"01:23:45"``; ``"--:--:--"`` when zero or
        negative.
    """
    if seconds <= 0:
        return "--:--:--"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def _format_size_mb(size_mb: float) -> str:
    """Format a size in megabytes as a human-readable string.

    Automatically selects MB, GB, or TB units.

    Args:
        size_mb: Size in megabytes (may be fractional).

    Returns:
        Formatted string like ``"1.23 GB"``; ``"0 MB"`` for zero or negative.
    """
    if size_mb <= 0:
        return "0 MB"
    if size_mb < 1024:
        return f"{size_mb:.0f} MB"
    gb = size_mb / 1024.0
    if gb < 1024.0:
        return f"{gb:.2f} GB"
    tb = gb / 1024.0
    return f"{tb:.2f} TB"


@dataclass(frozen=True)
class _SlotRow:
    """Normalized SABnzbd active queue slot."""

    name: str
    category: str
    progress_percent: float
    timeleft_seconds: int


class SABnzbdGetter(BaseGetter):
    """Getter for SABnzbd downloader queue, status, and history.

    Polls the SABnzbd HTTP API using the configured API key.  When
    ``base_url`` is empty the getter performs no work and returns an empty
    dict on every cycle (disabled / not configured).

    Args:
        store: Shared data store.
        base_url: SABnzbd server base URL (e.g. ``http://localhost:8080``).
            Leave empty to disable the getter entirely.
        api_key: SABnzbd API key for authentication.
        interval: Poll interval in seconds.
        timeout: HTTP request timeout in seconds.
        verify_tls: Verify TLS certificates when using HTTPS.
        max_slots: Maximum active slot rows to flatten into numbered keys.
        history_limit: Number of history entries to fetch per poll.
    """

    def __init__(  # noqa: PLR0913 -- explicit config wiring is clearer
        self,
        store: DataStore,
        base_url: str = "",
        api_key: str | None = None,
        interval: float = 5.0,
        timeout: float = 4.0,
        verify_tls: bool = True,
        max_slots: int = _MAX_SLOTS,
        history_limit: int = _HISTORY_LIMIT,
    ) -> None:
        """Initialise the SABnzbdGetter."""
        super().__init__(store, interval)
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key.strip() if isinstance(api_key, str) else ""
        self._timeout = timeout
        self._max_slots = max(1, max_slots)
        self._history_limit = max(1, history_limit)
        self._ssl_context: ssl.SSLContext | None = None
        if self._base_url.startswith("https://") and not verify_tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self._ssl_context = ctx

    def _build_url(self, mode: str, extra: dict[str, str] | None = None) -> str:
        """Build a SABnzbd API URL with authentication.

        Args:
            mode: API mode token (e.g. ``"queue"``, ``"history"``).
            extra: Optional additional query parameters.

        Returns:
            Full URL string with ``output=json`` and ``apikey`` appended.
        """
        params: dict[str, str] = {"mode": mode, "output": "json"}
        if self._api_key:
            params["apikey"] = self._api_key
        if extra:
            params.update(extra)
        return f"{self._base_url}/api?{urlencode(params)}"

    def _get_json(self, url: str) -> dict[str, Any]:
        """Perform a synchronous GET and return parsed JSON.

        Args:
            url: Full URL to fetch (scheme already validated by caller).

        Returns:
            Parsed JSON response body.

        Raises:
            RuntimeError: On HTTP 401/403 (auth failure) or any other error.
        """
        req = Request(url, method="GET")  # noqa: S310 -- caller validates scheme
        try:
            with urlopen(  # noqa: S310 -- caller validates scheme
                req,
                timeout=self._timeout,
                context=self._ssl_context,
            ) as resp:
                data: dict[str, Any] = json.loads(resp.read().decode())
                return data
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise RuntimeError("SABnzbd auth failed — check API key") from exc
            raise RuntimeError(f"SABnzbd HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"SABnzbd transport error: {exc}") from exc

    async def fetch(self) -> dict[str, StoreValue]:
        """Poll SABnzbd queue and history and return flattened store updates.

        Returns:
            Mapping of ``sabnzbd.*`` store keys to current values, or an
            empty dict when the getter is not configured.
        """
        if not self._base_url:
            return {}

        queue_url = self._build_url("queue")
        history_url = self._build_url("history", {"limit": str(self._history_limit)})

        queue_json, history_json = await asyncio.gather(
            asyncio.to_thread(self._get_json, queue_url),
            asyncio.to_thread(self._get_json, history_url),
        )

        updates: dict[str, StoreValue] = {}
        updates.update(self._parse_queue(queue_json))
        updates.update(self._parse_history(history_json))
        return updates

    def _parse_queue(self, raw: dict[str, Any]) -> dict[str, StoreValue]:
        """Extract queue metrics from a ``mode=queue`` response.

        Args:
            raw: Parsed JSON from the SABnzbd queue endpoint.

        Returns:
            Mapping of queue-related ``sabnzbd.*`` store keys.
        """
        queue = raw.get("queue", {}) if isinstance(raw.get("queue"), dict) else {}

        version = str(queue.get("version", ""))
        paused = 1 if bool(queue.get("paused", False)) else 0
        speed_str = str(queue.get("speed", "0"))
        mbleft_raw = queue.get("mbleft", "0")
        mbleft = float(mbleft_raw) if mbleft_raw else 0.0
        diskspace_raw = queue.get("diskspace1", "0")
        disk_free_gb = float(diskspace_raw) if diskspace_raw else 0.0
        timeleft_str = str(queue.get("timeleft", "0:00:00"))
        slots_raw: list[Any] = (
            queue.get("slots", [])
            if isinstance(queue.get("slots"), list)
            else []
        )

        rate_mbps = round(_parse_speed_mbps(speed_str), 2)
        eta_seconds = _parse_timeleft_seconds(timeleft_str)
        active_count = sum(
            1
            for s in slots_raw
            if str(s.get("status", "")).lower() == "downloading"
        )

        updates: dict[str, StoreValue] = {
            "sabnzbd.version": version,
            "sabnzbd.status.paused": paused,
            "sabnzbd.queue.total": len(slots_raw),
            "sabnzbd.queue.active_count": active_count,
            "sabnzbd.queue.remaining_mb": int(mbleft),
            "sabnzbd.queue.remaining_size": _format_size_mb(mbleft),
            "sabnzbd.rate.mbps": rate_mbps,
            "sabnzbd.eta_seconds": eta_seconds,
            "sabnzbd.eta_hms": _seconds_to_hms(eta_seconds),
            "sabnzbd.disk.free_gb": round(disk_free_gb, 2),
        }

        # Expand slot rows into numbered keys; always write all slots so that
        # stale entries are cleared when the queue shrinks.
        slot_rows = self._extract_slot_rows(slots_raw)
        for idx in range(1, self._max_slots + 1):
            pfx = f"sabnzbd.slot_{idx}"
            updates[f"{pfx}.name"] = ""
            updates[f"{pfx}.category"] = ""
            updates[f"{pfx}.progress_percent"] = 0.0
            updates[f"{pfx}.timeleft_seconds"] = 0
        for idx, row in enumerate(slot_rows[: self._max_slots], start=1):
            pfx = f"sabnzbd.slot_{idx}"
            updates[f"{pfx}.name"] = row.name
            updates[f"{pfx}.category"] = row.category
            updates[f"{pfx}.progress_percent"] = row.progress_percent
            updates[f"{pfx}.timeleft_seconds"] = row.timeleft_seconds

        return updates

    def _extract_slot_rows(self, slots: list[Any]) -> list[_SlotRow]:
        """Normalize raw queue slot dicts to typed :class:`_SlotRow` objects.

        Args:
            slots: Raw slot list from the SABnzbd queue response.

        Returns:
            Typed slot rows sorted by download progress descending.
        """
        rows: list[_SlotRow] = []
        for slot in slots:
            # SABnzbd uses "filename" as the display name and "cat" for category.
            name = str(slot.get("filename", slot.get("name", "Unknown")))
            category = str(slot.get("cat", slot.get("category", "")))
            percentage_raw = slot.get("percentage", "0")
            try:
                progress = float(percentage_raw)
            except (ValueError, TypeError):
                progress = 0.0
            timeleft_str = str(slot.get("timeleft", "0:00:00"))
            rows.append(
                _SlotRow(
                    name=name,
                    category=category,
                    progress_percent=round(progress, 1),
                    timeleft_seconds=_parse_timeleft_seconds(timeleft_str),
                )
            )
        rows.sort(key=lambda r: r.progress_percent, reverse=True)
        return rows

    def _parse_history(self, raw: dict[str, Any]) -> dict[str, StoreValue]:
        """Extract history counts from a ``mode=history`` response.

        Args:
            raw: Parsed JSON from the SABnzbd history endpoint.

        Returns:
            Mapping of history-related ``sabnzbd.*`` store keys.
        """
        history = raw.get("history", {}) if isinstance(raw.get("history"), dict) else {}
        slots: list[Any] = (
            history.get("slots", [])
            if isinstance(history.get("slots"), list)
            else []
        )

        success_count = sum(
            1 for s in slots if str(s.get("status", "")).lower() == "completed"
        )
        failed_count = sum(
            1
            for s in slots
            if str(s.get("status", "")).lower().startswith("failed")
        )

        return {
            "sabnzbd.history.success_count": success_count,
            "sabnzbd.history.failed_count": failed_count,
        }
