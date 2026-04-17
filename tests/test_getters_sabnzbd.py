"""Tests for :mod:`casedd.getters.sabnzbd` (issue #69)."""

from __future__ import annotations

from io import BytesIO
import json
import socket
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from casedd.data_store import DataStore
from casedd.getters.sabnzbd import (
    SABnzbdGetter,
    _format_size_mb,
    _parse_speed_mbps,
    _parse_timeleft_seconds,
    _resolve_hostname_to_ip,
    _seconds_to_hms,
)

# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store() -> DataStore:
    """Provide a fresh DataStore for each test."""
    return DataStore()


def _make_response(data: dict[str, Any]) -> MagicMock:
    """Build a minimal urlopen context-manager mock returning JSON."""
    body = json.dumps(data).encode()
    resp = MagicMock()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=None)
    resp.read = MagicMock(return_value=body)
    return resp


def _queue_payload(  # noqa: PLR0913 -- test fixture needs many optional fields
    paused: bool = False,
    speed: str = "5.00 M",
    mbleft: str = "512.00",
    diskspace1: str = "120.5",
    timeleft: str = "0:01:42",
    version: str = "3.7.1",
    slots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "queue": {
            "paused": paused,
            "speed": speed,
            "mbleft": mbleft,
            "diskspace1": diskspace1,
            "timeleft": timeleft,
            "version": version,
            "slots": slots or [],
        }
    }


def _history_payload(
    completed: int = 3,
    failed: int = 1,
) -> dict[str, Any]:
    slots = [{"status": "Completed"} for _ in range(completed)]
    slots += [{"status": "Failed"} for _ in range(failed)]
    return {"history": {"slots": slots}}


# ---------------------------------------------------------------------------
# Unit tests — pure helper functions
# ---------------------------------------------------------------------------


class TestResolveHostnameToIp:
    """Tests for :func:`_resolve_hostname_to_ip`."""

    def test_already_ip_unchanged(self) -> None:
        """URLs with IP-address hosts must be returned unchanged."""
        url = "http://192.168.1.100:8080"
        assert _resolve_hostname_to_ip(url) == url

    def test_localhost_unchanged(self) -> None:
        """``localhost`` resolves to ``127.0.0.1``, which is an IP — result differs."""
        result = _resolve_hostname_to_ip("http://localhost:8080")
        # localhost resolves to an IP; the netloc should use the IP form
        assert "localhost" not in result or result.startswith("http://127.")

    def test_empty_url_unchanged(self) -> None:
        """Empty string must be returned unchanged without raising."""
        assert _resolve_hostname_to_ip("") == ""

    def test_unresolvable_hostname_unchanged(self) -> None:
        """An unresolvable hostname must be returned unchanged (graceful fallback)."""
        url = "http://this-host-does-not-exist.invalid:9090"
        with patch("casedd.getters.sabnzbd.socket.gethostbyname", side_effect=socket.gaierror), \
             patch("casedd.getters.sabnzbd.socket.inet_aton", side_effect=OSError):
            assert _resolve_hostname_to_ip(url) == url

    def test_hostname_replaced_with_ip(self) -> None:
        """A resolvable hostname must be replaced with its IPv4 address."""
        with patch("casedd.getters.sabnzbd.socket.gethostbyname", return_value="10.0.0.5"), \
             patch("casedd.getters.sabnzbd.socket.inet_aton", side_effect=OSError):
            result = _resolve_hostname_to_ip("http://myserver:42069")
        assert result == "http://10.0.0.5:42069"

    def test_port_preserved(self) -> None:
        """The original port must be preserved after hostname resolution."""
        with patch("casedd.getters.sabnzbd.socket.gethostbyname", return_value="10.0.0.5"), \
             patch("casedd.getters.sabnzbd.socket.inet_aton", side_effect=OSError):
            result = _resolve_hostname_to_ip("http://myserver:12345")
        assert ":12345" in result


class TestParseSpeedMbps:
    """Tests for _parse_speed_mbps."""

    def test_megabytes(self) -> None:
        assert _parse_speed_mbps("5.00 M") == pytest.approx(5.0)

    def test_kilobytes(self) -> None:
        assert _parse_speed_mbps("1024 K") == pytest.approx(1.0)

    def test_gigabytes(self) -> None:
        assert _parse_speed_mbps("1.00 G") == pytest.approx(1024.0)

    def test_zero_with_space(self) -> None:
        assert _parse_speed_mbps("0 ") == pytest.approx(0.0)

    def test_empty(self) -> None:
        assert _parse_speed_mbps("") == pytest.approx(0.0)

    def test_invalid(self) -> None:
        assert _parse_speed_mbps("N/A") == pytest.approx(0.0)


class TestParseTimeleftSeconds:
    """Tests for _parse_timeleft_seconds."""

    def test_hms_format(self) -> None:
        assert _parse_timeleft_seconds("0:01:42") == 102

    def test_h_mm_format(self) -> None:
        assert _parse_timeleft_seconds("1:30") == 90

    def test_zero(self) -> None:
        assert _parse_timeleft_seconds("0:00:00") == 0

    def test_invalid(self) -> None:
        assert _parse_timeleft_seconds("N/A") == 0


class TestSecondsToHms:
    """Tests for _seconds_to_hms."""

    def test_normal(self) -> None:
        assert _seconds_to_hms(3723) == "01:02:03"

    def test_zero(self) -> None:
        assert _seconds_to_hms(0) == "--:--:--"

    def test_negative(self) -> None:
        assert _seconds_to_hms(-1) == "--:--:--"


class TestFormatSizeMb:
    """Tests for _format_size_mb."""

    def test_mb(self) -> None:
        assert _format_size_mb(512) == "512 MB"

    def test_gb(self) -> None:
        assert _format_size_mb(2048) == "2.00 GB"

    def test_tb(self) -> None:
        assert _format_size_mb(1024 * 1024) == "1.00 TB"

    def test_zero(self) -> None:
        assert _format_size_mb(0) == "0 MB"


# ---------------------------------------------------------------------------
# Integration-style getter tests
# ---------------------------------------------------------------------------


class TestSABnzbdGetter:
    """Tests for SABnzbdGetter.fetch()."""

    async def test_fetch_active_queue(self, store: DataStore) -> None:
        """Getter extracts speed, slots, and progress from a busy queue."""
        getter = SABnzbdGetter(
            store,
            base_url="http://localhost:8080",
            api_key="testkey",
        )
        slots = [
            {
                "status": "Downloading",
                "filename": "Show.S01E01.mkv",
                "cat": "tv",
                "percentage": "60",
                "timeleft": "0:02:00",
            },
            {
                "status": "Queued",
                "filename": "Movie.2024.mkv",
                "cat": "movies",
                "percentage": "0",
                "timeleft": "0:10:00",
            },
        ]
        queue_raw = _queue_payload(speed="8.00 M", mbleft="1024.00", slots=slots)
        history_raw = _history_payload(completed=5, failed=0)

        with patch(
            "casedd.getters.sabnzbd.urlopen",
            side_effect=[_make_response(queue_raw), _make_response(history_raw)],
        ):
            result = await getter.fetch()

        assert result["sabnzbd.rate.mbps"] == pytest.approx(8.0)
        assert result["sabnzbd.queue.total"] == 2
        assert result["sabnzbd.queue.active_count"] == 1
        assert result["sabnzbd.queue.remaining_mb"] == 1024
        assert result["sabnzbd.eta_hms"] != "--:--:--"
        assert result["sabnzbd.slot_1.name"] == "Show.S01E01.mkv"
        assert result["sabnzbd.slot_1.progress_percent"] == pytest.approx(60.0)
        assert result["sabnzbd.history.success_count"] == 5
        assert result["sabnzbd.history.failed_count"] == 0

    async def test_fetch_empty_queue(self, store: DataStore) -> None:
        """Empty queue returns zero-state without errors."""
        getter = SABnzbdGetter(store, base_url="http://localhost:8080", api_key="key")
        queue_raw = _queue_payload(speed="0 ", mbleft="0", timeleft="0:00:00")
        history_raw = _history_payload(0, 0)

        with patch(
            "casedd.getters.sabnzbd.urlopen",
            side_effect=[_make_response(queue_raw), _make_response(history_raw)],
        ):
            result = await getter.fetch()

        assert result["sabnzbd.queue.total"] == 0
        assert result["sabnzbd.rate.mbps"] == pytest.approx(0.0)
        assert result["sabnzbd.eta_hms"] == "--:--:--"
        assert result["sabnzbd.slot_1.name"] == ""
        assert result["sabnzbd.slot_1.progress_percent"] == pytest.approx(0.0)

    async def test_paused_state(self, store: DataStore) -> None:
        """Paused queue sets sabnzbd.status.paused to 1."""
        getter = SABnzbdGetter(store, base_url="http://localhost:8080", api_key="key")
        queue_raw = _queue_payload(paused=True)
        history_raw = _history_payload()

        with patch(
            "casedd.getters.sabnzbd.urlopen",
            side_effect=[_make_response(queue_raw), _make_response(history_raw)],
        ):
            result = await getter.fetch()

        assert result["sabnzbd.status.paused"] == 1

    async def test_auth_failure(self, store: DataStore) -> None:
        """HTTP 403 is converted to RuntimeError (getter health records error)."""
        getter = SABnzbdGetter(store, base_url="http://localhost:8080", api_key="bad")
        exc = HTTPError(url="", code=403, msg="Forbidden", hdrs=MagicMock(), fp=BytesIO())

        with (
            patch("casedd.getters.sabnzbd.urlopen", side_effect=exc),
            pytest.raises(RuntimeError, match="auth failed"),
        ):
            await getter.fetch()

    async def test_network_error(self, store: DataStore) -> None:
        """URLError is wrapped in RuntimeError."""
        getter = SABnzbdGetter(store, base_url="http://localhost:8080", api_key="key")
        with (
            patch("casedd.getters.sabnzbd.urlopen", side_effect=URLError("refused")),
            pytest.raises(RuntimeError, match="transport error"),
        ):
            await getter.fetch()

    async def test_disabled_when_no_base_url(self, store: DataStore) -> None:
        """Getter returns empty dict when base_url is empty (not configured)."""
        getter = SABnzbdGetter(store, base_url="")
        result = await getter.fetch()
        assert result == {}

    async def test_history_failed_count(self, store: DataStore) -> None:
        """Failed history entries are counted correctly."""
        getter = SABnzbdGetter(store, base_url="http://localhost:8080", api_key="key")
        history_raw = _history_payload(completed=2, failed=3)
        queue_raw = _queue_payload()

        with patch(
            "casedd.getters.sabnzbd.urlopen",
            side_effect=[_make_response(queue_raw), _make_response(history_raw)],
        ):
            result = await getter.fetch()

        assert result["sabnzbd.history.failed_count"] == 3
        assert result["sabnzbd.history.success_count"] == 2

    async def test_slot_rows_sorted_by_progress(self, store: DataStore) -> None:
        """Slot rows are sorted by progress descending (highest first)."""
        getter = SABnzbdGetter(store, base_url="http://localhost:8080", api_key="key")
        slots = [
            {
                "status": "Downloading",
                "filename": "First.mkv",
                "cat": "tv",
                "percentage": "30",
                "timeleft": "0:05:00",
            },
            {
                "status": "Downloading",
                "filename": "Second.mkv",
                "cat": "movies",
                "percentage": "80",
                "timeleft": "0:01:00",
            },
        ]
        queue_raw = _queue_payload(slots=slots)
        history_raw = _history_payload()

        with patch(
            "casedd.getters.sabnzbd.urlopen",
            side_effect=[_make_response(queue_raw), _make_response(history_raw)],
        ):
            result = await getter.fetch()

        # Second.mkv (80%) should be slot_1 after sorting
        assert result["sabnzbd.slot_1.name"] == "Second.mkv"
        assert result["sabnzbd.slot_2.name"] == "First.mkv"

    async def test_malformed_payload(self, store: DataStore) -> None:
        """Malformed / unexpected JSON shapes degrade gracefully to zero state."""
        getter = SABnzbdGetter(store, base_url="http://localhost:8080", api_key="key")
        queue_raw: dict[str, Any] = {}
        history_raw: dict[str, Any] = {}

        with patch(
            "casedd.getters.sabnzbd.urlopen",
            side_effect=[_make_response(queue_raw), _make_response(history_raw)],
        ):
            result = await getter.fetch()

        assert result["sabnzbd.queue.total"] == 0
        assert result["sabnzbd.rate.mbps"] == pytest.approx(0.0)
        assert result["sabnzbd.history.success_count"] == 0
