"""Tests for :mod:`casedd.getters.jellyfin` (issue #66)."""

from __future__ import annotations

from io import BytesIO
import json
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from casedd.data_store import DataStore
from casedd.getters.jellyfin import (
    JellyfinGetter,
    _normalize_sessions,
    _session_progress,
    _session_title,
)

# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store() -> DataStore:
    """Provide a fresh DataStore for each test."""
    return DataStore()


def _make_response(data: Any) -> MagicMock:
    """Build a minimal urlopen context-manager mock returning JSON."""
    body = json.dumps(data).encode()
    resp = MagicMock()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=None)
    resp.read = MagicMock(return_value=body)
    return resp


def _info_payload(name: str = "My Jellyfin", version: str = "10.9.1") -> dict[str, Any]:
    return {"ServerName": name, "Version": version}


def _session_payload(  # noqa: PLR0913 -- test helper with many optional fields
    user: str,
    title: str,
    media_type: str = "Movie",
    device: str = "Chrome",
    position_ticks: int = 500_000_000,
    runtime_ticks: int = 1_000_000_000,
    transcoding: bool = False,
    series_name: str = "",
) -> dict[str, Any]:
    now_playing: dict[str, Any] = {
        "Name": title,
        "Type": media_type,
        "RunTimeTicks": runtime_ticks,
    }
    if series_name:
        now_playing["SeriesName"] = series_name
    sess: dict[str, Any] = {
        "UserName": user,
        "DeviceName": device,
        "NowPlayingItem": now_playing,
        "PlayState": {"PositionTicks": position_ticks},
    }
    if transcoding:
        sess["TranscodingInfo"] = {"IsVideoDirect": False}
    return sess


def _counts_payload(
    movies: int = 100,
    series: int = 20,
    episodes: int = 400,
    albums: int = 10,
) -> dict[str, Any]:
    return {
        "MovieCount": movies,
        "SeriesCount": series,
        "EpisodeCount": episodes,
        "AlbumCount": albums,
    }


# ---------------------------------------------------------------------------
# Unit tests — pure helper functions
# ---------------------------------------------------------------------------


class TestSessionProgress:
    """Tests for _session_progress."""

    def test_midway(self) -> None:
        sess = _session_payload(
            "alice", "Movie", position_ticks=500_000_000, runtime_ticks=1_000_000_000
        )
        assert _session_progress(sess) == pytest.approx(50.0)

    def test_no_runtime(self) -> None:
        sess = _session_payload("alice", "Movie", runtime_ticks=0)
        assert _session_progress(sess) == pytest.approx(0.0)

    def test_near_end(self) -> None:
        sess = _session_payload(
            "alice", "Movie", position_ticks=9_900_000, runtime_ticks=10_000_000
        )
        assert _session_progress(sess) == pytest.approx(99.0)


class TestSessionTitle:
    """Tests for _session_title."""

    def test_movie_title(self) -> None:
        sess = _session_payload("alice", "Dune")
        assert _session_title(sess) == "Dune"

    def test_episode_title(self) -> None:
        sess = _session_payload("alice", "Pilot", series_name="Breaking Bad")
        assert _session_title(sess) == "Breaking Bad — Pilot"

    def test_missing_item(self) -> None:
        assert _session_title({}) == ""


class TestNormalizeSessions:
    """Tests for _normalize_sessions."""

    def test_filters_idle_sessions(self) -> None:
        """Sessions without NowPlayingItem are excluded."""
        idle = {"UserName": "bob", "DeviceName": "TV"}
        active = _session_payload("alice", "Dune")
        rows = _normalize_sessions([idle, active], max_sessions=6)
        assert len(rows) == 1
        assert rows[0].user == "alice"

    def test_respects_max_sessions(self) -> None:
        sessions = [_session_payload(f"user{i}", f"Movie{i}") for i in range(10)]
        rows = _normalize_sessions(sessions, max_sessions=3)
        assert len(rows) == 3

    def test_transcoding_flag(self) -> None:
        trans = _session_payload("alice", "Movie", transcoding=True)
        direct = _session_payload("bob", "Show")
        rows = _normalize_sessions([trans, direct], max_sessions=6)
        assert rows[0].is_transcoding is True
        assert rows[1].is_transcoding is False

    def test_empty_sessions(self) -> None:
        rows = _normalize_sessions([], max_sessions=6)
        assert rows == []


# ---------------------------------------------------------------------------
# Integration-style getter tests
# ---------------------------------------------------------------------------


class TestJellyfinGetter:
    """Tests for JellyfinGetter.fetch()."""

    async def test_fetch_normal(self, store: DataStore) -> None:
        """Normal response populates all expected store keys."""
        getter = JellyfinGetter(store, base_url="http://localhost:8096", api_key="key")
        sessions = [
            _session_payload("alice", "Dune", transcoding=False),
            _session_payload("bob", "Episode 1", media_type="Episode", series_name="Expanse"),
        ]

        with patch(
            "casedd.getters.jellyfin.urlopen",
            side_effect=[
                _make_response(_info_payload()),
                _make_response(sessions),
                _make_response(_counts_payload(movies=50, series=10, episodes=200, albums=5)),
            ],
        ):
            result = await getter.fetch()

        assert result["jellyfin.server.name"] == "My Jellyfin"
        assert result["jellyfin.server.version"] == "10.9.1"
        assert result["jellyfin.server.reachable"] == 1
        assert result["jellyfin.sessions.active_count"] == 2
        assert result["jellyfin.sessions.transcoding_count"] == 0
        assert result["jellyfin.sessions.direct_play_count"] == 2
        assert result["jellyfin.users.active_count"] == 2
        assert result["jellyfin.library.movies_count"] == 50
        assert result["jellyfin.library.series_count"] == 10
        assert result["jellyfin.library.episodes_count"] == 200
        assert result["jellyfin.library.music_albums_count"] == 5
        assert result["jellyfin.session_1.user"] == "alice"
        assert result["jellyfin.session_2.title"] == "Expanse — Episode 1"
        # sessions.rows: pipe-delimited string readable by jellyfin_now_playing
        rows_str = str(result["jellyfin.sessions.rows"])
        rows = [line for line in rows_str.splitlines() if line]
        assert len(rows) == 2
        parts0 = rows[0].split("|")
        assert parts0[0] == "alice"
        assert parts0[4] == "direct play"
        # is_transcoding per slot
        assert result["jellyfin.session_1.is_transcoding"] == 0

    async def test_zero_sessions(self, store: DataStore) -> None:
        """Zero active sessions produces zero-state counts without errors."""
        getter = JellyfinGetter(store, base_url="http://localhost:8096", api_key="key")

        with patch(
            "casedd.getters.jellyfin.urlopen",
            side_effect=[
                _make_response(_info_payload()),
                _make_response([]),
                _make_response(_counts_payload()),
            ],
        ):
            result = await getter.fetch()

        assert result["jellyfin.sessions.active_count"] == 0
        assert result["jellyfin.session_1.user"] == ""
        assert result["jellyfin.session_1.title"] == ""

    async def test_transcoding_count(self, store: DataStore) -> None:
        """Transcoding sessions are counted correctly and reflected in rows decision."""
        getter = JellyfinGetter(store, base_url="http://localhost:8096", api_key="key")
        sessions = [
            _session_payload("alice", "Movie", transcoding=True),
            _session_payload("bob", "Show", transcoding=False),
        ]

        with patch(
            "casedd.getters.jellyfin.urlopen",
            side_effect=[
                _make_response(_info_payload()),
                _make_response(sessions),
                _make_response(_counts_payload()),
            ],
        ):
            result = await getter.fetch()

        assert result["jellyfin.sessions.transcoding_count"] == 1
        assert result["jellyfin.sessions.direct_play_count"] == 1
        # rows string should label alice as transcode, bob as direct play
        rows_str = str(result["jellyfin.sessions.rows"])
        row_lines = [r for r in rows_str.splitlines() if r]
        assert row_lines[0].endswith("|transcode")
        assert row_lines[1].endswith("|direct play")
        # per-slot is_transcoding flag
        assert result["jellyfin.session_1.is_transcoding"] == 1
        assert result["jellyfin.session_2.is_transcoding"] == 0

    async def test_auth_failure(self, store: DataStore) -> None:
        """HTTP 401 is wrapped in RuntimeError."""
        getter = JellyfinGetter(store, base_url="http://localhost:8096", api_key="bad")
        exc = HTTPError(url="", code=401, msg="Unauthorized", hdrs=MagicMock(), fp=BytesIO())

        with (
            patch("casedd.getters.jellyfin.urlopen", side_effect=exc),
            pytest.raises(RuntimeError, match="auth failed"),
        ):
            await getter.fetch()

    async def test_network_error(self, store: DataStore) -> None:
        """URLError is wrapped in RuntimeError."""
        getter = JellyfinGetter(store, base_url="http://localhost:8096", api_key="key")
        with (
            patch("casedd.getters.jellyfin.urlopen", side_effect=URLError("refused")),
            pytest.raises(RuntimeError, match="transport error"),
        ):
            await getter.fetch()

    async def test_disabled_when_no_base_url(self, store: DataStore) -> None:
        """Getter returns empty dict when base_url is empty (not configured)."""
        getter = JellyfinGetter(store, base_url="")
        result = await getter.fetch()
        assert result == {}

    async def test_counts_failure_does_not_break_fetch(self, store: DataStore) -> None:
        """Library counts endpoint failure returns zeros without aborting fetch."""
        getter = JellyfinGetter(store, base_url="http://localhost:8096", api_key="key")
        # System/Info succeeds; Sessions succeeds; counts raises an HTTP error.
        counts_resp = HTTPError(url="", code=403, msg="Forbidden", hdrs=MagicMock(), fp=BytesIO())

        with patch(
            "casedd.getters.jellyfin.urlopen",
            side_effect=[
                _make_response(_info_payload()),
                _make_response([]),
                counts_resp,
            ],
        ):
            result = await getter.fetch()

        # Server info and sessions should still be populated.
        assert result["jellyfin.server.reachable"] == 1
        # Library counts default to 0 on failure.
        assert result["jellyfin.library.movies_count"] == 0

    async def test_session_slots_cleared_on_empty(self, store: DataStore) -> None:
        """All session slot keys are written even when there are no sessions."""
        getter = JellyfinGetter(
            store, base_url="http://localhost:8096", api_key="key", max_sessions=3
        )
        with patch(
            "casedd.getters.jellyfin.urlopen",
            side_effect=[
                _make_response(_info_payload()),
                _make_response([]),
                _make_response(_counts_payload()),
            ],
        ):
            result = await getter.fetch()

        for idx in range(1, 4):
            assert f"jellyfin.session_{idx}.user" in result
            assert result[f"jellyfin.session_{idx}.user"] == ""

    async def test_server_error(self, store: DataStore) -> None:
        """HTTP 500 is wrapped in RuntimeError."""
        getter = JellyfinGetter(store, base_url="http://localhost:8096", api_key="key")
        exc = HTTPError(
            url="", code=500, msg="Internal Server Error", hdrs=MagicMock(), fp=BytesIO()
        )
        with (
            patch("casedd.getters.jellyfin.urlopen", side_effect=exc),
            pytest.raises(RuntimeError, match="HTTP 500"),
        ):
            await getter.fetch()
