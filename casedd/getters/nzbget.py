"""NZBGet downloader integration.

Polls NZBGet via its documented JSON-RPC API and publishes flattened
``nzbget.*`` keys for queue tracking, status, and history.

Store keys written:
    - ``nzbget.version``
    - ``nzbget.status.download_paused``
    - ``nzbget.status.postprocess_paused``
    - ``nzbget.status.scan_paused``
    - ``nzbget.queue.total``
    - ``nzbget.queue.active_count``
    - ``nzbget.queue.remaining_mb``
    - ``nzbget.rate.mbps``
    - ``nzbget.eta_seconds``
    - ``nzbget.postprocess.active_count``
    - ``nzbget.history.success_count``
    - ``nzbget.history.failed_count``
    - ``nzbget.current_1.name`` ... ``nzbget.current_N.*``
    - ``nzbget.current_1.progress_percent``
    - ``nzbget.current_1.category``

Per-item current job rows are expanded into numbered keys:
    - ``nzbget.current_1.*`` ... ``nzbget.current_N.*``

API Reference: https://nzbget.net/api/
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import json
import logging
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)

# NZBGet API JSON-RPC methods used
_METHOD_STATUS = "status"
_METHOD_QUEUE = "listgroups"
_METHOD_HISTORY = "history"
_METHOD_VERSION = "version"


@dataclass(frozen=True)
class _NZBGetConfig:
    """Internal NZBGet configuration holder."""

    url: str
    username: str | None
    password: str | None
    timeout: float


@dataclass(frozen=True)
class _CurrentJob:
    """Normalized active NZBGet queue item row."""

    name: str
    progress_percent: float
    category: str


class NZBGetGetter(BaseGetter):
    """Getter for NZBGet downloader queue, status, and history.

    Args:
        store: Shared data store.
        url: NZBGet API server URL (e.g., http://localhost:6789).
        username: Username for RPC authentication (optional).
        password: Password for RPC authentication (optional).
        interval: Poll interval in seconds.
        timeout: HTTP timeout in seconds.
    """

    def __init__(  # noqa: PLR0913 -- config wrapper keeps params reasonable
        self,
        store: DataStore,
        url: str = "http://localhost:6789",
        username: str | None = None,
        password: str | None = None,
        interval: float = 5.0,
        timeout: float = 3.0,
        category_filter_regex: str | None = None,
    ) -> None:
        """Initialize the NZBGetGetter.

        Args:
            store: The shared :class:`~casedd.data_store.DataStore`.
            url: NZBGet server base URL.
            username: Optional username for HTTP auth.
            password: Optional password for HTTP auth.
            interval: Seconds between each poll (default: 5.0).
            timeout: HTTP request timeout in seconds (default: 3.0).
            category_filter_regex: Optional regex to hide matching categories for privacy.
        """
        super().__init__(store, interval)
        self._config = _NZBGetConfig(
            url=url.rstrip("/"),
            username=username,
            password=password,
            timeout=timeout,
        )
        # Compile regex for category filtering (if provided)
        self._category_filter_regex = (
            re.compile(category_filter_regex) if category_filter_regex else None
        )

    def _make_auth_header(self) -> dict[str, str] | None:
        """Create HTTP Basic Auth header if credentials are configured.

        Returns:
            Dict with 'Authorization' header, or None if no credentials.
        """
        if not self._config.username or not self._config.password:
            return None

        credentials = f"{self._config.username}:{self._config.password}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return {"Authorization": f"Basic {encoded}"}

    async def _rpc_call(self, method: str) -> dict[str, Any]:
        """Make a JSON-RPC 2.0 call to NZBGet.

        Args:
            method: RPC method name (e.g., 'status', 'listgroups').

        Returns:
            The parsed JSON response 'result' field.

        Raises:
            RuntimeError: On RPC error, HTTP error, or network failure.
        """
        payload = json.dumps({"method": method, "params": [], "jsonrpc": "2.0"})
        url = f"{self._config.url}/jsonrpc"

        # Validate URL scheme
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            msg = f"Invalid NZBGet URL scheme: {parsed.scheme}"
            raise ValueError(msg)

        headers = {"Content-Type": "application/json"}
        auth = self._make_auth_header()
        if auth:
            headers.update(auth)

        request = Request(  # noqa: S310 -- scheme validated above
            url, data=payload.encode(), headers=headers, method="POST"
        )

        def blocking_request() -> dict[str, Any]:
            try:
                with urlopen(request, timeout=self._config.timeout) as response:  # noqa: S310 -- scheme validated
                    data = json.loads(response.read().decode())
                    if "error" in data and data["error"] is not None:
                        raise RuntimeError(f"NZBGet RPC error: {data['error']}")
                    result: dict[str, Any] = data.get("result", {})
                    return result
            except (HTTPError, URLError) as exc:
                msg = f"NZBGet HTTP error: {exc}"
                raise RuntimeError(msg) from exc

        return await asyncio.to_thread(blocking_request)

    async def fetch(self) -> dict[str, StoreValue]:
        """Poll NZBGet and return normalized data store updates.

        Returns:
            Dict of dotted key → value pairs for the data store.

        Raises:
            RuntimeError: On RPC error or network failure.
        """
        updates: dict[str, StoreValue] = {}

        # Fetch version (returns string directly, not a dict)
        version_data = await self._rpc_call(_METHOD_VERSION)
        version = str(version_data) if isinstance(version_data, str) else str(
            version_data.get("version", "unknown")
        )
        updates["nzbget.version"] = version

        # Fetch status and queue in parallel
        status_data, queue_data, history_data = await asyncio.gather(
            self._rpc_call(_METHOD_STATUS),
            self._rpc_call(_METHOD_QUEUE),
            self._rpc_call(_METHOD_HISTORY),
        )

        # Process status
        updates["nzbget.status.download_paused"] = bool(
            status_data.get("DownloadPaused")
        )
        updates["nzbget.status.postprocess_paused"] = bool(
            status_data.get("PostPaused")
        )
        updates["nzbget.status.scan_paused"] = bool(
            status_data.get("ScanPaused")
        )

        # Process queue metrics
        queue_items: list[Any] = queue_data if isinstance(queue_data, list) else []
        active_count = sum(
            1 for item in queue_items if bool(item.get("ActiveDownloads", 0) > 0)
        )
        total_mb = sum(item.get("RemainingSizeMB", 0) for item in queue_items)
        current_rate = float(status_data.get("DownloadRate", 0)) / 1024.0 / 1024.0
        eta_seconds = int(
            total_mb / current_rate if current_rate > 0 else 0
        )

        updates["nzbget.queue.total"] = len(queue_items)
        updates["nzbget.queue.active_count"] = active_count
        updates["nzbget.queue.remaining_mb"] = int(total_mb)
        updates["nzbget.rate.mbps"] = round(current_rate, 2)
        updates["nzbget.eta_seconds"] = eta_seconds

        # Process postprocess status
        postprocess_count = sum(
            1
            for item in queue_items
            if bool(item.get("PostProcessing", False))
        )
        updates["nzbget.postprocess.active_count"] = postprocess_count

        # Process history
        history_items: list[Any] = history_data if isinstance(history_data, list) else []
        success_count = sum(
            1
            for item in history_items
            if (item.get("Status") == "SUCCESS" or item.get("Status", 0) == 0)
        )
        failed_count = sum(
            1
            for item in history_items
            if (
                item.get("Status") in ("FAILURE", "DELETED")
                or item.get("Status", 0) in (1, 3)
            )
        )

        updates["nzbget.history.success_count"] = success_count
        updates["nzbget.history.failed_count"] = failed_count

        # Process current jobs (first 3 for display)
        current_jobs = self._extract_current_jobs(queue_items)
        for idx, job in enumerate(current_jobs[:3], start=1):
            prefix = f"nzbget.current_{idx}"
            updates[f"{prefix}.name"] = job.name
            updates[f"{prefix}.progress_percent"] = job.progress_percent
            updates[f"{prefix}.category"] = job.category

        return updates

    def _extract_current_jobs(
        self, queue_items: list[dict[str, Any]]
    ) -> list[_CurrentJob]:
        """Extract actively downloading jobs from queue list.

        Filters out jobs matching the category filter regex (if configured).

        Args:
            queue_items: Raw queue list from NZBGet API.

        Returns:
            List of current job rows, sorted by progress descending.
        """
        jobs: list[_CurrentJob] = []

        for item in queue_items:
            if item.get("ActiveDownloads", 0) > 0 or item.get("PausedSizeMB", 0) == 0:
                # Item is active or not entirely paused
                name = item.get("NZBName", "Unknown")
                remaining = int(item.get("RemainingSizeMB", 0))
                total = int(item.get("FileSizeMB", 0))
                progress = (
                    100.0
                    if total == 0
                    else round(100.0 * (total - remaining) / total, 1)
                )
                category = item.get("Category", "")

                # Skip categories matching filter regex (for privacy)
                if self._category_filter_regex and self._category_filter_regex.search(
                    category
                ):
                    continue

                jobs.append(
                    _CurrentJob(
                        name=name,
                        progress_percent=progress,
                        category=category,
                    )
                )

        # Sort by progress descending (highest progress first)
        jobs.sort(key=lambda j: j.progress_percent, reverse=True)
        return jobs
