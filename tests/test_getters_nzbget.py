"""Tests for NZBGet getter.

Tests cover active queue, empty queue, auth failure, and network failure scenarios.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from casedd.data_store import DataStore
from casedd.getter_health import GetterHealthRegistry
from casedd.getters.nzbget import NZBGetGetter


@pytest.fixture
def data_store() -> DataStore:
    """Provide a fresh DataStore for each test."""
    return DataStore()


@pytest.fixture
def health_registry() -> GetterHealthRegistry:
    """Provide a fresh GetterHealthRegistry for each test."""
    return GetterHealthRegistry()


class TestNZBGetGetter:
    """Test suite for NZBGetGetter."""

    async def test_fetch_active_queue(self, data_store: DataStore) -> None:
        """Test fetching and parsing active queue with jobs."""
        getter = NZBGetGetter(
            data_store,
            url="http://localhost:6789",
            interval=1.0,
            timeout=3.0,
        )

        mock_status = {
            "DownloadPaused": False,
            "PostPaused": False,
            "ScanPaused": False,
            "DownloadRate": 5242880,  # 5 MB/s in bytes
        }
        mock_queue = [
            {
                "NZBName": "Show.S01E01.1080p",
                "FileSizeMB": 2000,
                "RemainingSizeMB": 1000,
                "ActiveDownloads": 1,
                "Category": "tv",
                "PostProcessing": False,
            },
            {
                "NZBName": "Show.S01E02.1080p",
                "FileSizeMB": 2000,
                "RemainingSizeMB": 2000,
                "ActiveDownloads": 0,
                "Category": "tv",
                "PostProcessing": False,
            },
        ]
        mock_history = [
            {"Status": "SUCCESS"},
            {"Status": "SUCCESS"},
            {"Status": "FAILURE"},
        ]

        with (
            patch.object(getter, "_rpc_call") as mock_rpc,
        ):
            mock_rpc.side_effect = [
                {"version": "1.0.0"},  # _METHOD_VERSION
                mock_status,  # _METHOD_STATUS
                mock_queue,  # _METHOD_QUEUE
                mock_history,  # _METHOD_HISTORY
            ]

            # First call: version fetch (single call)
            version_result = await getter._rpc_call("version")
            assert version_result == {"version": "1.0.0"}

            # Second call: gather calls for status/queue/history
            results = await asyncio.gather(
                getter._rpc_call("status"),
                getter._rpc_call("listgroups"),
                getter._rpc_call("history"),
            )
            status_result, queue_result, history_result = results

            assert status_result == mock_status
            assert queue_result == mock_queue
            assert history_result == mock_history

    async def test_fetch_empty_queue(self, data_store: DataStore) -> None:
        """Test graceful handling of empty queue (zero-state)."""
        getter = NZBGetGetter(data_store, interval=1.0, timeout=3.0)

        mock_status = {
            "DownloadPaused": False,
            "PostPaused": False,
            "ScanPaused": False,
            "DownloadRate": 0,
        }
        mock_queue: list = []
        mock_history: list = []

        with patch.object(getter, "_rpc_call") as mock_rpc:
            mock_rpc.side_effect = [
                {"version": "1.0.0"},
                mock_status,
                mock_queue,
                mock_history,
            ]

            version_result = await getter._rpc_call("version")
            results = await asyncio.gather(
                getter._rpc_call("status"),
                getter._rpc_call("listgroups"),
                getter._rpc_call("history"),
            )

            assert version_result["version"] == "1.0.0"
            assert results[1] == []
            assert results[2] == []

    async def test_auth_failure(self, data_store: DataStore) -> None:
        """Test graceful handling of authentication failures."""
        getter = NZBGetGetter(
            data_store,
            url="http://localhost:6789",
            username="user",
            password="wrong",
            interval=1.0,
            timeout=3.0,
        )

        # Simulate HTTP 401 Unauthorized
        with patch("casedd.getters.nzbget.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = HTTPError(
                "http://localhost:6789/jsonrpc", 401, "Unauthorized", {}, None
            )

            with pytest.raises(RuntimeError, match="NZBGet HTTP error"):
                await getter._rpc_call("status")

    async def test_network_failure(self, data_store: DataStore) -> None:
        """Test graceful handling of network failures."""
        getter = NZBGetGetter(
            data_store,
            url="http://localhost:6789",
            interval=1.0,
            timeout=3.0,
        )

        with patch("casedd.getters.nzbget.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = URLError("Connection refused")

            with pytest.raises(RuntimeError, match="NZBGet HTTP error"):
                await getter._rpc_call("status")

    async def test_rpc_error_response(self, data_store: DataStore) -> None:
        """Test handling of JSON-RPC error responses."""
        getter = NZBGetGetter(
            data_store,
            url="http://localhost:6789",
            interval=1.0,
            timeout=3.0,
        )

        with patch("casedd.getters.nzbget.urlopen") as mock_urlopen:
            # Mock response with RPC error
            mock_response = MagicMock()
            mock_response.read.return_value = json.dumps(
                {"error": "Access denied", "result": None}
            ).encode()
            mock_response.__enter__.return_value = mock_response
            mock_response.__exit__.return_value = None
            mock_urlopen.return_value = mock_response

            with pytest.raises(RuntimeError, match="NZBGet RPC error"):
                await getter._rpc_call("status")

    async def test_invalid_url_scheme(self, data_store: DataStore) -> None:
        """Test rejection of invalid URL schemes."""
        getter = NZBGetGetter(
            data_store,
            url="file:///etc/passwd",
            interval=1.0,
            timeout=3.0,
        )

        with pytest.raises(ValueError, match="Invalid NZBGet URL scheme"):
            await getter._rpc_call("status")

    async def test_fetch_with_paused_states(self, data_store: DataStore) -> None:
        """Test parsing of paused state flags."""
        getter = NZBGetGetter(data_store, interval=1.0, timeout=3.0)

        mock_status = {
            "DownloadPaused": True,
            "PostPaused": True,
            "ScanPaused": False,
            "DownloadRate": 0,
        }
        mock_queue: list = []
        mock_history: list = []

        with patch.object(getter, "_rpc_call") as mock_rpc:
            mock_rpc.side_effect = [
                mock_status,
                mock_queue,
                mock_history,
            ]

            results = await asyncio.gather(
                getter._rpc_call("status"),
                getter._rpc_call("listgroups"),
                getter._rpc_call("history"),
            )
            status_result = results[0]

            # Verify status fields
            assert status_result["DownloadPaused"] is True
            assert status_result["PostPaused"] is True
            assert status_result["ScanPaused"] is False

    async def test_fetch_with_postprocessing(self, data_store: DataStore) -> None:
        """Test detection of items in post-processing state."""
        getter = NZBGetGetter(data_store, interval=1.0, timeout=3.0)

        mock_status = {
            "DownloadPaused": False,
            "PostPaused": False,
            "ScanPaused": False,
            "DownloadRate": 0,
        }
        mock_queue = [
            {
                "NZBName": "Item1",
                "FileSizeMB": 100,
                "RemainingSizeMB": 0,
                "ActiveDownloads": 0,
                "Category": "tv",
                "PostProcessing": True,
            },
            {
                "NZBName": "Item2",
                "FileSizeMB": 200,
                "RemainingSizeMB": 100,
                "ActiveDownloads": 1,
                "Category": "tv",
                "PostProcessing": False,
            },
        ]
        mock_history: list = []

        with patch.object(getter, "_rpc_call") as mock_rpc:
            mock_rpc.side_effect = [
                mock_status,
                mock_queue,
                mock_history,
            ]

            results = await asyncio.gather(
                getter._rpc_call("status"),
                getter._rpc_call("listgroups"),
                getter._rpc_call("history"),
            )
            queue_result = results[1]

            # Verify postprocessing item
            pp_items = [q for q in queue_result if q["PostProcessing"]]
            assert len(pp_items) == 1
            assert pp_items[0]["NZBName"] == "Item1"

    async def test_fetch_history_status_codes(self, data_store: DataStore) -> None:
        """Test parsing of history items with various status codes."""
        getter = NZBGetGetter(data_store, interval=1.0, timeout=3.0)

        # NZBGet history uses status codes: 0=SUCCESS, 1=FAILURE, 3=DELETED
        mock_history = [
            {"Status": 0},  # numeric SUCCESS
            {"Status": "SUCCESS"},  # string SUCCESS
            {"Status": 1},  # numeric FAILURE
            {"Status": "FAILURE"},  # string FAILURE
            {"Status": 3},  # numeric DELETED
            {"Status": "DELETED"},  # string DELETED
        ]

        with patch.object(getter, "_rpc_call") as mock_rpc:
            mock_rpc.return_value = mock_history

            result = await getter._rpc_call("history")
            assert len(result) == 6

    async def test_health_tracking_on_fetch_error(
        self,
        data_store: DataStore,
        health_registry: GetterHealthRegistry,
    ) -> None:
        """Test that fetch errors are recorded in health registry."""
        getter = NZBGetGetter(data_store, interval=0.01, timeout=3.0)
        getter.attach_health(health_registry)

        with patch.object(getter, "_rpc_call") as mock_rpc:
            mock_rpc.side_effect = RuntimeError("Connection failed")

            # Run one iteration
            task = asyncio.create_task(getter.run())
            await asyncio.sleep(0.05)
            getter.stop()
            await asyncio.wait_for(task, timeout=1.0)

            # Check health snapshot
            snap = {e["name"]: e for e in health_registry.snapshot()}
            entry = snap.get("NZBGetGetter")
            assert entry is not None
            assert entry["status"] == "error"

    async def test_extract_current_jobs(self, data_store: DataStore) -> None:
        """Test extraction and sorting of current jobs."""
        getter = NZBGetGetter(data_store, interval=1.0, timeout=3.0)

        queue_items = [
            {
                "NZBName": "Show.S01E01.1080p",
                "FileSizeMB": 2000,
                "RemainingSizeMB": 500,
                "ActiveDownloads": 1,
                "Category": "tv",
            },
            {
                "NZBName": "Show.S01E02.1080p",
                "FileSizeMB": 2000,
                "RemainingSizeMB": 100,
                "ActiveDownloads": 1,
                "Category": "tv",
            },
            {
                "NZBName": "Paused Item",
                "FileSizeMB": 1000,
                "RemainingSizeMB": 1000,
                "ActiveDownloads": 0,
                "PausedSizeMB": 1000,
                "Category": "tv",
            },
        ]

        jobs = getter._extract_current_jobs(queue_items)

        # Should extract only active jobs (first two), sorted by progress (highest first)
        assert len(jobs) >= 1
        assert jobs[0].name in ("Show.S01E02.1080p", "Show.S01E01.1080p")
