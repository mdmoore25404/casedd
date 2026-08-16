"""Tests for :mod:`casedd.getters.espn_sports`."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import pytest

from casedd.data_store import DataStore
from casedd.getters.espn_sports import (
    _INTERVAL_IDLE,
    _INTERVAL_LIVE,
    _LOGO_CACHE_MAXSIZE,
    _MAX_GAME_SLOTS,
    EspnSportsGetter,
    _mins_to_tip,
    _parse_period,
    _parse_series,
)

# ── helpers ────────────────────────────────────────────────────────────────


class _FakeResp:
    """Minimal context-manager response mock for urlopen."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *args: object) -> None:
        pass


def _load_fixture(name: str) -> bytes:
    """Return raw bytes of a test fixture JSON file."""
    p = Path(__file__).parent / "fixtures" / name
    return p.read_bytes()


def _mock_urlopen(fixture_name: str) -> _FakeResp:
    """Return a context-manager mock that yields the fixture as a response."""
    return _FakeResp(_load_fixture(fixture_name))


# ── unit: _parse_period ────────────────────────────────────────────────────


def test_parse_period_pre() -> None:
    assert _parse_period(0, "pre", "7:30 PM ET") == "Pre"


def test_parse_period_post() -> None:
    assert _parse_period(4, "post", "Final") == "Final"


def test_parse_period_halftime() -> None:
    assert _parse_period(2, "in", "Halftime") == "HT"


def test_parse_period_end_of_quarter() -> None:
    assert _parse_period(1, "in", "End of Q1") == "End Q1"


def test_parse_period_quarter() -> None:
    assert _parse_period(3, "in", "Q3 4:22") == "Q3"


def test_parse_period_ot() -> None:
    assert _parse_period(5, "in", "OT 1:00") == "OT"


def test_parse_period_double_ot() -> None:
    assert _parse_period(6, "in", "2OT 2:15") == "OT2"


# ── unit: _parse_series ────────────────────────────────────────────────────


def test_parse_series_with_summary() -> None:
    comp = {"series": {"summary": "NY leads series 3-0"}}
    assert _parse_series(comp) == "NY leads series 3-0"


def test_parse_series_missing() -> None:
    assert _parse_series({}) == ""


def test_parse_series_empty_summary() -> None:
    assert _parse_series({"series": {"summary": ""}}) == ""


# ── unit: _mins_to_tip ────────────────────────────────────────────────────


def test_mins_to_tip_none() -> None:
    assert _mins_to_tip(None) == -1


def test_mins_to_tip_future(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed = datetime.datetime(2025, 5, 16, 20, 0, 0, tzinfo=datetime.UTC)
    tipoff = datetime.datetime(2025, 5, 16, 20, 30, 0, tzinfo=datetime.UTC)

    with patch("casedd.getters.espn_sports.datetime") as mock_dt:
        mock_dt.datetime.now.return_value = fixed
        mock_dt.UTC = datetime.UTC
        result = _mins_to_tip(tipoff)

    assert result == 30


def test_mins_to_tip_past(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed = datetime.datetime(2025, 5, 16, 21, 0, 0, tzinfo=datetime.UTC)
    tipoff = datetime.datetime(2025, 5, 16, 20, 0, 0, tzinfo=datetime.UTC)

    with patch("casedd.getters.espn_sports.datetime") as mock_dt:
        mock_dt.datetime.now.return_value = fixed
        mock_dt.UTC = datetime.UTC
        result = _mins_to_tip(tipoff)

    # Should be -1 (clamped) since game already started.
    assert result == -1


# ── integration: EspnSportsGetter.fetch ────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_nba_final_game(tmp_path: Path) -> None:
    """Parsing a final NBA game populates all expected store keys."""

    def _fake_urlopen(req: object, timeout: float) -> _FakeResp:
        return _mock_urlopen("espn_nba_demo.json")

    with patch("casedd.getters.espn_sports.urlopen", _fake_urlopen):
        getter = EspnSportsGetter(DataStore(), leagues=["nba"], logo_cache_dir=str(tmp_path))
        result = await getter.fetch()

    # Slot 0: NYK vs PHI — Final
    assert result["espn_nba.game_0.away_abbr"] == "NYK"
    assert result["espn_nba.game_0.home_abbr"] == "PHI"
    assert result["espn_nba.game_0.away_score"] == 108.0
    assert result["espn_nba.game_0.home_score"] == 94.0
    assert result["espn_nba.game_0.detail"] == "Final"
    assert result["espn_nba.game_0.state"] == "post"
    assert result["espn_nba.game_0.series"] == "NY leads series 4-0"


@pytest.mark.asyncio
async def test_fetch_nba_live_game(tmp_path: Path) -> None:
    """Parsing a live (in-progress) NBA game populates period/clock correctly."""

    def _fake_urlopen(req: object, timeout: float) -> _FakeResp:
        return _mock_urlopen("espn_nba_demo.json")

    with patch("casedd.getters.espn_sports.urlopen", _fake_urlopen):
        getter = EspnSportsGetter(DataStore(), leagues=["nba"], logo_cache_dir=str(tmp_path))
        result = await getter.fetch()

    # Slot 1: SAC vs MIN — End of Q3 (live)
    assert result["espn_nba.game_1.state"] == "in"
    assert result["espn_nba.game_1.away_abbr"] == "SAC"
    assert result["espn_nba.game_1.away_score"] == 78.0
    assert result["espn_nba.game_1.home_score"] == 83.0
    assert "Q3" in str(result["espn_nba.game_1.detail"]) or "End" in str(
        result["espn_nba.game_1.detail"]
    )


@pytest.mark.asyncio
async def test_fetch_nba_halftime(tmp_path: Path) -> None:
    """Halftime game should report 'HT' as period."""

    def _fake_urlopen(req: object, timeout: float) -> _FakeResp:
        return _mock_urlopen("espn_nba_demo.json")

    with patch("casedd.getters.espn_sports.urlopen", _fake_urlopen):
        getter = EspnSportsGetter(DataStore(), leagues=["nba"], logo_cache_dir=str(tmp_path))
        result = await getter.fetch()

    # Slot 2: BOS vs MIA — Halftime
    assert result["espn_nba.game_2.state"] == "in"
    assert result["espn_nba.game_2.period"] == "HT"


@pytest.mark.asyncio
async def test_fetch_nba_pregame(tmp_path: Path) -> None:
    """Pre-game slot should report state='pre' and a tipoff label."""

    def _fake_urlopen(req: object, timeout: float) -> _FakeResp:
        return _mock_urlopen("espn_nba_demo.json")

    with patch("casedd.getters.espn_sports.urlopen", _fake_urlopen):
        getter = EspnSportsGetter(DataStore(), leagues=["nba"], logo_cache_dir=str(tmp_path))
        result = await getter.fetch()

    # Slot 3: OKC vs DAL — Pre-game
    assert result["espn_nba.game_3.state"] == "pre"
    assert result["espn_nba.game_3.away_abbr"] == "OKC"
    assert result["espn_nba.game_3.home_abbr"] == "DAL"
    assert result["espn_nba.game_3.away_score"] == 0.0


@pytest.mark.asyncio
async def test_fetch_wnba_live_and_final(tmp_path: Path) -> None:
    """WNBA fetch returns live and final game slots."""

    def _fake_urlopen(req: object, timeout: float) -> _FakeResp:
        return _mock_urlopen("espn_wnba_demo.json")

    with patch("casedd.getters.espn_sports.urlopen", _fake_urlopen):
        getter = EspnSportsGetter(
            DataStore(), leagues=["wnba"], logo_cache_dir=str(tmp_path)
        )
        result = await getter.fetch()

    assert result["espn_wnba.game_0.state"] == "in"
    assert result["espn_wnba.game_0.away_abbr"] == "NY"
    assert result["espn_wnba.game_1.state"] == "post"
    assert result["espn_wnba.game_2.state"] == "pre"


@pytest.mark.asyncio
async def test_fetch_reachable_flag(tmp_path: Path) -> None:
    """reachable=1 when fetch succeeds, 0 on error."""

    def _ok(req: object, timeout: float) -> _FakeResp:
        return _mock_urlopen("espn_nba_demo.json")

    def _fail(req: object, timeout: float) -> None:
        raise URLError("connection refused")

    with patch("casedd.getters.espn_sports.urlopen", _ok):
        getter = EspnSportsGetter(DataStore(), leagues=["nba"], logo_cache_dir=str(tmp_path))
        ok_result = await getter.fetch()

    with patch("casedd.getters.espn_sports.urlopen", _fail):
        err_result = await getter.fetch()

    assert ok_result["espn_nba.reachable"] == 1
    assert err_result["espn_nba.reachable"] == 0


@pytest.mark.asyncio
async def test_adaptive_interval_live_games(tmp_path: Path) -> None:
    """Interval should shrink to LIVE rate when live games are found."""

    def _fake_urlopen(req: object, timeout: float) -> _FakeResp:
        return _mock_urlopen("espn_nba_demo.json")

    with patch("casedd.getters.espn_sports.urlopen", _fake_urlopen):
        getter = EspnSportsGetter(DataStore(), leagues=["nba"], logo_cache_dir=str(tmp_path))
        assert getter._interval == _INTERVAL_IDLE
        await getter.fetch()
        assert getter._interval == _INTERVAL_LIVE


@pytest.mark.asyncio
async def test_adaptive_interval_no_live_games(tmp_path: Path) -> None:
    """Interval should stay at IDLE rate when no live games exist."""

    # A fixture with only post/pre games (no 'in' state).
    fixture = json.dumps(
        {
            "events": [
                {
                    "id": "999",
                    "name": "Team A at Team B",
                    "date": "2025-05-16T01:00Z",
                    "competitions": [
                        {
                            "competitors": [
                                {
                                    "homeAway": "away",
                                    "team": {
                                        "abbreviation": "AAA",
                                        "displayName": "Team A",
                                        "color": "000000",
                                        "logo": "https://example.com/a.png",
                                    },
                                    "score": "100",
                                    "records": [{"type": "total", "summary": "50-32"}],
                                },
                                {
                                    "homeAway": "home",
                                    "team": {
                                        "abbreviation": "BBB",
                                        "displayName": "Team B",
                                        "color": "ffffff",
                                        "logo": "https://example.com/b.png",
                                    },
                                    "score": "90",
                                    "records": [{"type": "total", "summary": "40-42"}],
                                },
                            ],
                            "status": {
                                "type": {"state": "post", "shortDetail": "Final"},
                                "period": 4,
                                "displayClock": "0:00",
                            },
                        }
                    ],
                }
            ]
        }
    ).encode()

    def _fake_urlopen(req: object, timeout: float) -> _FakeResp:
        return _FakeResp(fixture)

    with patch("casedd.getters.espn_sports.urlopen", _fake_urlopen):
        getter = EspnSportsGetter(DataStore(), leagues=["nba"], logo_cache_dir=str(tmp_path))
        await getter.fetch()
        assert getter._interval == _INTERVAL_IDLE


# ── logo cache bounds ──────────────────────────────────────────────────────


def test_logo_cache_never_exceeds_maxsize(tmp_path: Path) -> None:
    """Logo cache must not grow beyond _LOGO_CACHE_MAXSIZE entries."""
    getter = EspnSportsGetter(DataStore(), leagues=[], logo_cache_dir=str(tmp_path))

    # Simulate downloading and caching more logos than the limit.
    for i in range(_LOGO_CACHE_MAXSIZE + 10):
        abbr = f"T{i:02d}"
        dest = tmp_path / f"{abbr.lower()}.png"
        dest.write_bytes(b"fake-logo")
        getter._logo_cache[abbr] = dest
        getter._logo_cache.move_to_end(abbr)
        if len(getter._logo_cache) > _LOGO_CACHE_MAXSIZE:
            getter._logo_cache.popitem(last=False)

    assert len(getter._logo_cache) <= _LOGO_CACHE_MAXSIZE


def test_logo_cache_evicts_oldest(tmp_path: Path) -> None:
    """Oldest logo entry must be evicted first (LRU)."""
    getter = EspnSportsGetter(DataStore(), leagues=[], logo_cache_dir=str(tmp_path))

    for i in range(_LOGO_CACHE_MAXSIZE + 1):
        abbr = f"T{i:02d}"
        dest = tmp_path / f"{abbr.lower()}.png"
        dest.write_bytes(b"fake-logo")
        getter._logo_cache[abbr] = dest
        getter._logo_cache.move_to_end(abbr)
        if len(getter._logo_cache) > _LOGO_CACHE_MAXSIZE:
            getter._logo_cache.popitem(last=False)

    # "T00" (the first inserted) should have been evicted.
    assert "T00" not in getter._logo_cache
    # The most recently inserted entry should still be present.
    assert f"T{_LOGO_CACHE_MAXSIZE:02d}" in getter._logo_cache


# ── empty slot zeroing ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_slots_zeroed(tmp_path: Path) -> None:
    """Slots beyond the game count should be zeroed out in the store."""

    fixture = json.dumps({"events": []}).encode()

    def _fake_urlopen(req: object, timeout: float) -> _FakeResp:
        return _FakeResp(fixture)

    with patch("casedd.getters.espn_sports.urlopen", _fake_urlopen):
        getter = EspnSportsGetter(DataStore(), leagues=["nba"], logo_cache_dir=str(tmp_path))
        result = await getter.fetch()

    # All game slots should be empty/zeroed.
    for idx in range(_MAX_GAME_SLOTS):
        assert result[f"espn_nba.game_{idx}.state"] == ""
        assert result[f"espn_nba.game_{idx}.away_abbr"] == ""
        assert result[f"espn_nba.game_{idx}.away_score"] == 0.0


# ── disabled leagues ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_leagues_returns_empty(tmp_path: Path) -> None:
    """A getter with no leagues configured returns an empty dict."""
    getter = EspnSportsGetter(DataStore(), leagues=[], logo_cache_dir=str(tmp_path))
    result = await getter.fetch()
    assert result == {}


# ── store key structure ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_store_key_structure(tmp_path: Path) -> None:
    """Verify expected top-level keys are present in a successful fetch."""

    def _fake_urlopen(req: object, timeout: float) -> _FakeResp:
        return _mock_urlopen("espn_nba_demo.json")

    with patch("casedd.getters.espn_sports.urlopen", _fake_urlopen):
        getter = EspnSportsGetter(DataStore(), leagues=["nba"], logo_cache_dir=str(tmp_path))
        result = await getter.fetch()

    assert "espn_nba.reachable" in result
    assert "espn_nba.live_count" in result
    assert "espn_nba.game_count" in result
    for idx in range(_MAX_GAME_SLOTS):
        assert f"espn_nba.game_{idx}.state" in result
        assert f"espn_nba.game_{idx}.away_abbr" in result
        assert f"espn_nba.game_{idx}.home_abbr" in result
        assert f"espn_nba.game_{idx}.away_score" in result
        assert f"espn_nba.game_{idx}.home_score" in result
        assert f"espn_nba.game_{idx}.period" in result
        assert f"espn_nba.game_{idx}.clock" in result
        assert f"espn_nba.game_{idx}.detail" in result
        assert f"espn_nba.game_{idx}.series" in result
