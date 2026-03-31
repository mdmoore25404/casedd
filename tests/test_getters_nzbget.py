"""Tests for NZBGet getter.

Tests cover active queue, empty queue, auth failure, and network failure scenarios.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from casedd.data_store import DataStore
from casedd.getter_health import GetterHealthRegistry
from casedd.getters.nzbget import NZBGetGetter, _format_size_mb, _seconds_to_hms


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

        # All three items have RemainingSizeMB > 0 so all are included.
        # Sorted by progress descending: S01E02 (95%) > S01E01 (75%) > Paused (0%)
        assert len(jobs) == 3
        assert jobs[0].name == "Show.S01E02.1080p"
        assert jobs[1].name == "Show.S01E01.1080p"

    async def test_completed_items_excluded(self, data_store: DataStore) -> None:
        """Completed items (RemainingSizeMB == 0) must not appear in current jobs.

        NZBGet keeps completed entries in the queue list briefly before moving them
        to history.  Without filtering they appear at 100% progress indefinitely.
        """
        getter = NZBGetGetter(data_store, interval=1.0, timeout=3.0)

        queue_items = [
            {
                "NZBName": "Completed.Movie",
                "FileSizeMB": 5000,
                "RemainingSizeMB": 0,   # fully downloaded, awaiting history move
                "ActiveDownloads": 0,
                "Category": "movies",
            },
            {
                "NZBName": "In.Progress.Show",
                "FileSizeMB": 2000,
                "RemainingSizeMB": 800,
                "ActiveDownloads": 1,
                "Category": "tv",
            },
        ]

        jobs = getter._extract_current_jobs(queue_items)

        assert len(jobs) == 1
        assert jobs[0].name == "In.Progress.Show"

    async def test_all_completed_returns_empty(self, data_store: DataStore) -> None:
        """When all queue items are complete, the current jobs list is empty."""
        getter = NZBGetGetter(data_store, interval=1.0, timeout=3.0)

        queue_items = [
            {
                "NZBName": "Done.1",
                "FileSizeMB": 1000,
                "RemainingSizeMB": 0,
                "ActiveDownloads": 0,
                "Category": "movies",
            },
            {
                "NZBName": "Done.2",
                "FileSizeMB": 2000,
                "RemainingSizeMB": 0,
                "ActiveDownloads": 0,
                "Category": "tv",
            },
        ]

        jobs = getter._extract_current_jobs(queue_items)
        assert jobs == []

    async def test_category_filter_regex(self, data_store: DataStore) -> None:
        """Regex privacy filter redacts matching categories instead of dropping rows."""
        getter = NZBGetGetter(
            data_store,
            interval=1.0,
            timeout=3.0,
            category_filter_regex=r"(xxx|adult)",
        )

        queue_items = [
            {
                "NZBName": "Movie.2024.1080p",
                "FileSizeMB": 5000,
                "RemainingSizeMB": 2500,
                "ActiveDownloads": 1,
                "Category": "movies",
            },
            {
                "NZBName": "Content.Private",
                "FileSizeMB": 3000,
                "RemainingSizeMB": 1500,
                "ActiveDownloads": 1,
                "Category": "xxx > premium",
            },
            {
                "NZBName": "Show.S01E01",
                "FileSizeMB": 2000,
                "RemainingSizeMB": 1000,
                "ActiveDownloads": 1,
                "Category": "tv",
            },
            {
                "NZBName": "Adult.Content",
                "FileSizeMB": 4000,
                "RemainingSizeMB": 2000,
                "ActiveDownloads": 1,
                "Category": "adult content",
            },
        ]

        jobs = getter._extract_current_jobs(queue_items)

        assert len(jobs) == 4
        categories = {job.category for job in jobs}
        assert "movies" in categories
        assert "tv" in categories
        assert "[hidden]" in categories

        hidden_names = [job.name for job in jobs if job.category == "[hidden]"]
        assert hidden_names == ["[hidden]", "[hidden]"]

    async def test_fetch_includes_hidden_current_count_and_active_percent(
        self,
        data_store: DataStore,
    ) -> None:
        """Fetch emits hidden-safe current stats and active-not-paused percent."""
        getter = NZBGetGetter(
            data_store,
            interval=1.0,
            timeout=3.0,
            category_filter_regex=r"(xxx|adult)",
        )

        mock_status = {
            "DownloadPaused": False,
            "PostPaused": False,
            "ScanPaused": False,
            "DownloadRate": 1048576,
        }
        mock_queue = [
            {
                "NZBName": "Visible.Active",
                "FileSizeMB": 1000,
                "RemainingSizeMB": 400,
                "ActiveDownloads": 1,
                "PausedSizeMB": 0,
                "Category": "tv",
                "PostProcessing": False,
            },
            {
                "NZBName": "Hidden.Active",
                "FileSizeMB": 800,
                "RemainingSizeMB": 400,
                "ActiveDownloads": 1,
                "PausedSizeMB": 0,
                "Category": "adult content",
                "PostProcessing": False,
            },
            {
                "NZBName": "Hidden.Paused",
                "FileSizeMB": 600,
                "RemainingSizeMB": 600,
                "ActiveDownloads": 0,
                "PausedSizeMB": 600,
                "Category": "xxx > premium",
                "PostProcessing": False,
            },
        ]
        mock_history: list[dict[str, str]] = []

        with patch.object(getter, "_rpc_call") as mock_rpc:
            mock_rpc.side_effect = [
                {"version": "1.0.0"},
                mock_status,
                mock_queue,
                mock_history,
            ]

            updates = await getter.fetch()

        assert updates["nzbget.queue.current_count"] == 3
        assert updates["nzbget.queue.active_count"] == 2
        assert updates["nzbget.queue.active_download_percent"] == 66.7
        assert updates["nzbget.current_1.name"] != "Hidden.Active"
        assert updates["nzbget.current_2.name"] == "[hidden]"

    async def test_fetch_excludes_paused_from_active_percent(
        self,
        data_store: DataStore,
    ) -> None:
        """Paused entries are excluded from active-download percentage."""
        getter = NZBGetGetter(data_store, interval=1.0, timeout=3.0)

        mock_status = {
            "DownloadPaused": False,
            "PostPaused": False,
            "ScanPaused": False,
            "DownloadRate": 0,
        }
        mock_queue = [
            {
                "NZBName": "Active.Item",
                "FileSizeMB": 500,
                "RemainingSizeMB": 250,
                "ActiveDownloads": 1,
                "PausedSizeMB": 0,
                "Category": "tv",
                "PostProcessing": False,
            },
            {
                "NZBName": "Paused.Item",
                "FileSizeMB": 500,
                "RemainingSizeMB": 500,
                "ActiveDownloads": 1,
                "PausedSizeMB": 500,
                "Category": "tv",
                "PostProcessing": False,
            },
        ]

        with patch.object(getter, "_rpc_call") as mock_rpc:
            mock_rpc.side_effect = [
                {"version": "1.0.0"},
                mock_status,
                mock_queue,
                [],
            ]
            updates = await getter.fetch()

        assert updates["nzbget.queue.current_count"] == 2
        assert updates["nzbget.queue.active_count"] == 1
        assert updates["nzbget.queue.active_download_percent"] == 50.0

    async def test_fetch_clears_current_slots_when_queue_empties(
        self,
        data_store: DataStore,
    ) -> None:
        """Fetch clears previously populated current_* slots when no jobs remain."""
        getter = NZBGetGetter(data_store, interval=1.0, timeout=3.0)

        with patch.object(getter, "_rpc_call") as mock_rpc:
            mock_rpc.side_effect = [
                {"version": "1.0.0"},
                {
                    "DownloadPaused": False,
                    "PostPaused": False,
                    "ScanPaused": False,
                    "DownloadRate": 524288,
                },
                [
                    {
                        "NZBName": "Visible.Active",
                        "FileSizeMB": 100,
                        "RemainingSizeMB": 50,
                        "ActiveDownloads": 1,
                        "PausedSizeMB": 0,
                        "Category": "tv",
                        "PostProcessing": False,
                    }
                ],
                [],
                {"version": "1.0.0"},
                {
                    "DownloadPaused": False,
                    "PostPaused": True,
                    "ScanPaused": False,
                    "DownloadRate": 0,
                },
                [],
                [],
            ]

            first_updates = await getter.fetch()
            second_updates = await getter.fetch()

        assert first_updates["nzbget.current_1.name"] == "Visible.Active"
        assert first_updates["nzbget.queue.current_count"] == 1

        assert second_updates["nzbget.queue.current_count"] == 0
        assert second_updates["nzbget.current_1.name"] == ""
        assert second_updates["nzbget.current_1.progress_percent"] == 0.0
        assert second_updates["nzbget.current_1.category"] == ""
        assert second_updates["nzbget.current_2.name"] == ""
        assert second_updates["nzbget.current_3.name"] == ""

    async def test_category_filter_no_regex(self, data_store: DataStore) -> None:
        """Test that all categories are included when no filter regex is set."""
        getter = NZBGetGetter(
            data_store,
            interval=1.0,
            timeout=3.0,
            category_filter_regex=None,
        )

        queue_items = [
            {
                "NZBName": "Content1",
                "FileSizeMB": 1000,
                "RemainingSizeMB": 500,
                "ActiveDownloads": 1,
                "Category": "xxx",
            },
            {
                "NZBName": "Content2",
                "FileSizeMB": 2000,
                "RemainingSizeMB": 1000,
                "ActiveDownloads": 1,
                "Category": "adult",
            },
            {
                "NZBName": "Content3",
                "FileSizeMB": 1500,
                "RemainingSizeMB": 750,
                "ActiveDownloads": 1,
                "Category": "tv",
            },
        ]

        jobs = getter._extract_current_jobs(queue_items)

        # Should include all items when no filter is set
        assert len(jobs) == 3
        categories = {job.category for job in jobs}
        assert "xxx" in categories
        assert "adult" in categories
        assert "tv" in categories


class TestHelperFunctions:
    """Unit tests for module-level helper functions."""

    @pytest.mark.parametrize("seconds,expected", [
        (0, "--:--:--"),
        (-5, "--:--:--"),
        (1, "00:00:01"),
        (59, "00:00:59"),
        (60, "00:01:00"),
        (3599, "00:59:59"),
        (3600, "01:00:00"),
        (3661, "01:01:01"),
        (86399, "23:59:59"),
        (90061, "25:01:01"),  # >24h edge case
    ])
    def test_seconds_to_hms(self, seconds: int, expected: str) -> None:
        """_seconds_to_hms formats seconds as HH:MM:SS."""
        assert _seconds_to_hms(seconds) == expected

    @pytest.mark.parametrize("size_mb,expected", [
        (0, "0 MB"),
        (-1, "0 MB"),
        (1, "1 MB"),
        (512, "512 MB"),
        (1023, "1023 MB"),
        (1024, "1.00 GB"),
        (2048, "2.00 GB"),
        (1536, "1.50 GB"),
        (1048576, "1.00 TB"),
        (2097152, "2.00 TB"),
    ])
    def test_format_size_mb(self, size_mb: int, expected: str) -> None:
        """_format_size_mb converts MB to human-readable MB/GB/TB strings."""
        assert _format_size_mb(size_mb) == expected
