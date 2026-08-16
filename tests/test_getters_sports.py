"""Tests for :mod:`casedd.getters.sports` (issue #127)."""

from __future__ import annotations

import asyncio
import datetime
import json
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest

from casedd.config import SportsTeamConfig
from casedd.data_store import DataStore
from casedd.getters.sports import (
    SportsGetter,
    _days_until,
    _EventSummary,
    _format_time_utc,
    _is_today,
    _label_date,
    _parse_result,
    _ResolvedTeam,
    _score_string,
    _SportsCfg,
    _today_date,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store() -> DataStore:
    """Fresh DataStore for each test."""
    return DataStore()


@pytest.fixture
def default_cfg() -> _SportsCfg:
    """Default _SportsCfg for tests."""
    return _SportsCfg(api_key="123", timeout=5.0, max_teams=10, recent_window_hours=48)


def _make_urlopen_response(data: Any) -> MagicMock:
    """Build a minimal urlopen context-manager mock returning JSON data."""
    body = json.dumps(data).encode()
    resp = MagicMock()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=None)
    resp.read = MagicMock(return_value=body)
    return resp


def _team_dict(
    team_id: str = "134867",
    name: str = "Los Angeles Lakers",
    sport: str = "Basketball",
    league: str = "NBA",
) -> dict[str, Any]:
    """Build a minimal TheSportsDB team dict."""
    return {
        "idTeam": team_id,
        "strTeam": name,
        "strSport": sport,
        "strLeague": league,
    }


def _event_dict(  # noqa: PLR0913  -- test helper needs all event fields
    date: str = "2025-04-19",
    time: str = "23:30:00",
    home_team: str = "Houston Rockets",
    away_team: str = "Los Angeles Lakers",
    home_id: str = "134868",
    away_id: str = "134867",
    home_score: str | None = None,
    away_score: str | None = None,
    status: str = "NS",
) -> dict[str, Any]:
    """Build a minimal TheSportsDB event dict."""
    return {
        "dateEvent": date,
        "strTime": time,
        "strHomeTeam": home_team,
        "strAwayTeam": away_team,
        "idHomeTeam": home_id,
        "idAwayTeam": away_id,
        "intHomeScore": home_score,
        "intAwayScore": away_score,
        "strStatus": status,
    }


def _resolved_team(
    name: str = "Los Angeles Lakers",
    team_id: str = "134867",
    sport: str = "NBA",
    league: str = "NBA",
) -> _ResolvedTeam:
    """Build a _ResolvedTeam fixture."""
    words = name.split()
    short = words[-1] if len(words) > 1 else name
    return _ResolvedTeam(
        config_team=name,
        config_sport=sport,
        team_id=team_id,
        name=name,
        short_name=short,
        sport=sport,
        league=league,
    )


# ---------------------------------------------------------------------------
# Unit tests — pure helper functions
# ---------------------------------------------------------------------------


class TestTodayDate:
    """Tests for _today_date."""

    def test_returns_date(self) -> None:
        d = _today_date()
        assert isinstance(d, datetime.date)

    def test_is_today(self) -> None:
        # Should match today according to UTC time.
        d = _today_date()
        now = datetime.datetime.now(tz=datetime.UTC)
        assert d == now.date()


class TestLabelDate:
    """Tests for _label_date."""

    def test_empty_string(self) -> None:
        assert _label_date("") == ""

    def test_invalid_format(self) -> None:
        assert _label_date("not-a-date") == "not-a-date"

    def test_today(self) -> None:
        today = _today_date().isoformat()
        assert _label_date(today) == "Today"

    def test_tomorrow(self) -> None:
        tomorrow = (_today_date() + datetime.timedelta(days=1)).isoformat()
        assert _label_date(tomorrow) == "Tomorrow"

    def test_yesterday(self) -> None:
        yesterday = (_today_date() - datetime.timedelta(days=1)).isoformat()
        assert _label_date(yesterday) == "Yesterday"

    def test_within_week_includes_day_name(self) -> None:
        in_3_days = (_today_date() + datetime.timedelta(days=3)).isoformat()
        label = _label_date(in_3_days)
        # Should start with a three-letter day name (Mon, Tue, etc.)
        assert len(label) > 4
        assert label[:3].isalpha()

    def test_far_future_no_day_name(self) -> None:
        far = (_today_date() + datetime.timedelta(days=30)).isoformat()
        label = _label_date(far)
        # Should be "Mon DD" style without full weekday prefix
        # i.e., starts with a 3-letter month abbreviation (Jan-Dec)
        assert label[:3].isalpha()
        # No weekday prefix (which would be 3-char + space then month)
        # The label should have exactly one space between month and day number.
        parts = label.split()
        assert len(parts) == 2
        assert parts[1].isdigit()


class TestFormatTimeUtc:
    """Tests for _format_time_utc."""

    def test_empty(self) -> None:
        assert _format_time_utc("") == ""

    def test_midnight(self) -> None:
        assert _format_time_utc("00:00:00") == "12:00 AM UTC"

    def test_noon(self) -> None:
        assert _format_time_utc("12:00:00") == "12:00 PM UTC"

    def test_evening(self) -> None:
        assert _format_time_utc("23:30:00") == "11:30 PM UTC"

    def test_morning(self) -> None:
        assert _format_time_utc("09:05:00") == "9:05 AM UTC"

    def test_invalid(self) -> None:
        assert _format_time_utc("bad") == ""

    def test_partial(self) -> None:
        # Only hours — should still work (seconds optional)
        result = _format_time_utc("18:30")
        assert result == "6:30 PM UTC"


class TestParseResult:
    """Tests for _parse_result."""

    def _make_event(
        self,
        home_score: int | None,
        away_score: int | None,
        status: str,
        is_home: bool,
    ) -> _EventSummary:
        return _EventSummary(
            team_id="134867",
            date_str="2025-04-13",
            time_str="23:30:00",
            opponent="Utah Jazz",
            home_away="Home" if is_home else "Away",
            home_score=home_score,
            away_score=away_score,
            status=status,
            is_home=is_home,
        )

    def test_not_started_returns_empty(self) -> None:
        evt = self._make_event(None, None, "NS", True)
        assert _parse_result(evt) == ""

    def test_win_as_home(self) -> None:
        evt = self._make_event(131, 107, "FT", True)
        assert _parse_result(evt) == "W"

    def test_loss_as_home(self) -> None:
        evt = self._make_event(107, 131, "FT", True)
        assert _parse_result(evt) == "L"

    def test_win_as_away(self) -> None:
        evt = self._make_event(107, 131, "FT", False)
        assert _parse_result(evt) == "W"

    def test_loss_as_away(self) -> None:
        evt = self._make_event(131, 107, "FT", False)
        assert _parse_result(evt) == "L"

    def test_draw(self) -> None:
        evt = self._make_event(1, 1, "FT", True)
        assert _parse_result(evt) == "D"

    def test_non_ft_status_suppressed(self) -> None:
        # Status "1H" (first half, in progress) should return ""
        evt = self._make_event(2, 0, "1H", True)
        assert _parse_result(evt) == ""


class TestScoreString:
    """Tests for _score_string."""

    def _evt(self, hs: int | None, as_: int | None, is_home: bool) -> _EventSummary:
        return _EventSummary(
            team_id="x",
            date_str="2025-04-13",
            time_str="",
            opponent="",
            home_away="Home" if is_home else "Away",
            home_score=hs,
            away_score=as_,
            status="FT",
            is_home=is_home,
        )

    def test_home_win_format(self) -> None:
        assert _score_string(self._evt(131, 107, True)) == "131-107"

    def test_away_win_format(self) -> None:
        # Away team won 131-107 (away_score=131, home_score=107, is_home=False)
        assert _score_string(self._evt(107, 131, False)) == "131-107"

    def test_none_scores(self) -> None:
        assert _score_string(self._evt(None, None, True)) == ""


class TestDaysUntil:
    """Tests for _days_until."""

    def test_empty(self) -> None:
        assert _days_until("") == -999

    def test_invalid(self) -> None:
        assert _days_until("bad") == -999

    def test_today(self) -> None:
        assert _days_until(_today_date().isoformat()) == 0

    def test_tomorrow(self) -> None:
        d = (_today_date() + datetime.timedelta(days=1)).isoformat()
        assert _days_until(d) == 1

    def test_past(self) -> None:
        d = (_today_date() - datetime.timedelta(days=5)).isoformat()
        assert _days_until(d) == -5


class TestIsToday:
    """Tests for _is_today."""

    def test_empty(self) -> None:
        assert _is_today("") is False

    def test_invalid(self) -> None:
        assert _is_today("2025-99-99") is False

    def test_today(self) -> None:
        assert _is_today(_today_date().isoformat()) is True

    def test_not_today(self) -> None:
        other = (_today_date() + datetime.timedelta(days=1)).isoformat()
        assert _is_today(other) is False


# ---------------------------------------------------------------------------
# Unit tests — SportsGetter (with mocked HTTP)
# ---------------------------------------------------------------------------


class TestSportsGetterNoTeams:
    """SportsGetter returns empty dict when no teams are configured."""

    def test_empty_teams(self, store: DataStore, default_cfg: _SportsCfg) -> None:
        getter = SportsGetter(store, teams=[], cfg=default_cfg)
        result = asyncio.run(getter.fetch())
        assert result == {}


class TestSportsGetterTeamResolution:
    """Test team ID resolution against mocked TheSportsDB search."""

    def _make_getter(
        self,
        store: DataStore,
        cfg: _SportsCfg,
        teams: list[SportsTeamConfig],
    ) -> SportsGetter:
        return SportsGetter(store, teams=teams, cfg=cfg)

    def test_single_result_no_sport_hint(
        self, store: DataStore, default_cfg: _SportsCfg
    ) -> None:
        team_cfg = SportsTeamConfig(team="Los Angeles Lakers", sport="")
        getter = self._make_getter(store, default_cfg, [team_cfg])

        search_resp = _make_urlopen_response(
            {"teams": [_team_dict("134867", "Los Angeles Lakers", "Basketball", "NBA")]}
        )
        events_resp = _make_urlopen_response({"events": []})

        with patch("casedd.getters.sports.urlopen", side_effect=[
            search_resp, events_resp, events_resp,
        ]):
            getter._resolve_all()

        assert len(getter._resolved) == 1
        assert getter._resolved[0].team_id == "134867"
        assert getter._resolved[0].short_name == "Lakers"
        assert getter._resolved[0].sport == "Basketball"

    def test_sport_hint_disambiguates_multiple_results(
        self, store: DataStore, default_cfg: _SportsCfg
    ) -> None:
        """West Virginia returns both NCAA Football and Basketball."""
        football_team = _team_dict("136976", "West Virginia", "American Football", "NCAA")
        basketball_team = _team_dict("138596", "West Virginia", "Basketball", "NCAA Basketball")
        team_cfg = SportsTeamConfig(team="West Virginia", sport="Basketball")
        getter = self._make_getter(store, default_cfg, [team_cfg])

        search_resp = _make_urlopen_response(
            {"teams": [football_team, basketball_team]}
        )
        with patch("casedd.getters.sports.urlopen", return_value=search_resp):
            getter._resolve_all()

        assert len(getter._resolved) == 1
        assert getter._resolved[0].team_id == "138596"
        assert getter._resolved[0].sport == "Basketball"

    def test_no_match_logs_warning_and_skips(
        self, store: DataStore, default_cfg: _SportsCfg
    ) -> None:
        team_cfg = SportsTeamConfig(team="Unknown Team XYZ", sport="")
        getter = self._make_getter(store, default_cfg, [team_cfg])

        search_resp = _make_urlopen_response({"teams": None})
        with patch("casedd.getters.sports.urlopen", return_value=search_resp):
            getter._resolve_all()

        assert getter._ids_resolved is True
        assert len(getter._resolved) == 0

    def test_network_error_logs_and_marks_resolved(
        self, store: DataStore, default_cfg: _SportsCfg
    ) -> None:
        team_cfg = SportsTeamConfig(team="Lakers", sport="")
        getter = self._make_getter(store, default_cfg, [team_cfg])

        with patch("casedd.getters.sports.urlopen", side_effect=URLError("timeout")):
            getter._resolve_all()

        assert getter._ids_resolved is True
        assert len(getter._resolved) == 0


class TestSportsGetterFetch:
    """Integration tests for the full fetch() cycle with mocked HTTP."""

    def _run_fetch(
        self,
        store: DataStore,
        cfg: _SportsCfg,
        teams: list[SportsTeamConfig],
        urlopen_side_effects: list[Any],
    ) -> dict[str, Any]:
        getter = SportsGetter(store, teams=teams, cfg=cfg)
        with patch("casedd.getters.sports.urlopen", side_effect=urlopen_side_effects):
            return asyncio.run(getter.fetch())

    def test_full_fetch_populates_keys(
        self, store: DataStore, default_cfg: _SportsCfg
    ) -> None:
        team_cfg = SportsTeamConfig(team="Los Angeles Lakers", sport="NBA")

        search_resp = _make_urlopen_response(
            {"teams": [_team_dict("134867", "Los Angeles Lakers", "Basketball", "NBA")]}
        )
        next_resp = _make_urlopen_response({
            "events": [_event_dict(
                date="2025-04-19",
                time="23:30:00",
                home_team="Houston Rockets",
                away_team="Los Angeles Lakers",
                home_id="134868",
                away_id="134867",
                status="NS",
            )]
        })
        last_resp = _make_urlopen_response({
            "results": [_event_dict(
                date="2025-04-13",
                time="23:30:00",
                home_team="Los Angeles Lakers",
                away_team="Utah Jazz",
                home_id="134867",
                away_id="134000",
                home_score="131",
                away_score="107",
                status="FT",
            )]
        })

        result = self._run_fetch(
            store, default_cfg, [team_cfg],
            [search_resp, next_resp, last_resp],
        )

        assert result["sports.reachable"] == 1
        assert result["sports.followed_count"] == 1
        assert result["sports.team_1.name"] == "Los Angeles Lakers"
        assert result["sports.team_1.short_name"] == "Lakers"
        assert result["sports.team_1.sport"] == "Basketball"
        # Next game is away (Lakers are idAwayTeam)
        assert result["sports.team_1.next.home_away"] == "Away"
        assert result["sports.team_1.next.opponent"] == "Houston Rockets"
        # Last game is a win (Lakers home, scored 131 vs 107)
        assert result["sports.team_1.last.result"] == "W"
        assert result["sports.team_1.last.score"] == "131-107"
        # upcoming_rows should contain the team label
        upcoming: str = str(result.get("sports.upcoming_rows", ""))
        assert "Lakers" in upcoming
        assert "Rockets" in upcoming

    def test_all_resolutions_fail_returns_reachable_zero(
        self, store: DataStore, default_cfg: _SportsCfg
    ) -> None:
        team_cfg = SportsTeamConfig(team="Unknown FC", sport="")
        search_resp = _make_urlopen_response({"teams": None})

        result = self._run_fetch(store, default_cfg, [team_cfg], [search_resp])
        assert result.get("sports.reachable") == 0

    def test_stale_slots_cleared(self, store: DataStore, default_cfg: _SportsCfg) -> None:
        """Slots beyond the resolved team count should be cleared."""
        # Pre-seed a stale slot in the store.
        store.set("sports.team_2.name", "Stale Team")

        team_cfg = SportsTeamConfig(team="Los Angeles Lakers", sport="")
        search_resp = _make_urlopen_response(
            {"teams": [_team_dict("134867", "Los Angeles Lakers")]}
        )
        next_resp = _make_urlopen_response({"events": []})
        last_resp = _make_urlopen_response({"results": []})

        result = self._run_fetch(
            store, default_cfg, [team_cfg],
            [search_resp, next_resp, last_resp],
        )

        # Slot 2 name should be cleared to empty string
        assert result.get("sports.team_2.name") == ""


class TestParseEvent:
    """Tests for SportsGetter._parse_event (static method)."""

    def test_returns_none_on_non_dict(self) -> None:
        assert SportsGetter._parse_event(None, "x") is None
        assert SportsGetter._parse_event([], "x") is None
        assert SportsGetter._parse_event("string", "x") is None

    def test_home_team(self) -> None:
        raw = _event_dict(
            home_team="Lakers", away_team="Jazz",
            home_id="134867", away_id="134000",
        )
        evt = SportsGetter._parse_event(raw, "134867")
        assert evt is not None
        assert evt.is_home is True
        assert evt.home_away == "Home"
        assert evt.opponent == "Jazz"

    def test_away_team(self) -> None:
        raw = _event_dict(
            home_team="Jazz", away_team="Lakers",
            home_id="134000", away_id="134867",
        )
        evt = SportsGetter._parse_event(raw, "134867")
        assert evt is not None
        assert evt.is_home is False
        assert evt.home_away == "Away"
        assert evt.opponent == "Jazz"

    def test_scores_parsed_when_present(self) -> None:
        raw = _event_dict(home_score="131", away_score="107", status="FT")
        evt = SportsGetter._parse_event(raw, "whatever")
        assert evt is not None
        assert evt.home_score == 131
        assert evt.away_score == 107

    def test_scores_none_when_not_started(self) -> None:
        raw = _event_dict(home_score=None, away_score=None, status="NS")
        evt = SportsGetter._parse_event(raw, "whatever")
        assert evt is not None
        assert evt.home_score is None
        assert evt.away_score is None

    def test_invalid_score_string_gives_none(self) -> None:
        raw = _event_dict()
        raw["intHomeScore"] = "invalid"
        raw["intAwayScore"] = "bad"
        evt = SportsGetter._parse_event(raw, "whatever")
        assert evt is not None
        assert evt.home_score is None
        assert evt.away_score is None


class TestBuildRows:
    """Tests for SportsGetter._build_rows row string construction."""

    def _getter(self, store: DataStore, cfg: _SportsCfg) -> SportsGetter:
        return SportsGetter(store, teams=[], cfg=cfg)

    def test_upcoming_row_format(
        self, store: DataStore, default_cfg: _SportsCfg
    ) -> None:
        getter = self._getter(store, default_cfg)
        team = _resolved_team("Los Angeles Lakers", "134867", "NBA")
        future = (_today_date() + datetime.timedelta(days=3)).isoformat()
        next_evt = SportsGetter._parse_event(
            _event_dict(date=future, home_id="OTHER", away_id="134867"),
            "134867",
        )
        cutoff = datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(hours=48)
        upcoming, _, _ = getter._build_rows([team], [next_evt], [None], cutoff)
        assert "Lakers" in upcoming
        assert "NBA" in upcoming
        assert "vs" in upcoming

    def test_recent_row_format(
        self, store: DataStore, default_cfg: _SportsCfg
    ) -> None:
        getter = self._getter(store, default_cfg)
        team = _resolved_team("Los Angeles Lakers", "134867", "NBA")
        # A recent game (yesterday)
        yesterday = (_today_date() - datetime.timedelta(days=1)).isoformat()
        last_evt = _EventSummary(
            team_id="134867",
            date_str=yesterday,
            time_str="23:30:00",
            opponent="Utah Jazz",
            home_away="Home",
            home_score=131,
            away_score=107,
            status="FT",
            is_home=True,
        )
        cutoff = datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(hours=48)
        _, recent, _ = getter._build_rows([team], [None], [last_evt], cutoff)
        assert "Lakers" in recent
        assert "W" in recent
        assert "Jazz" in recent

    def test_today_row_added(
        self, store: DataStore, default_cfg: _SportsCfg
    ) -> None:
        getter = self._getter(store, default_cfg)
        team = _resolved_team("Washington Wizards", "134884", "NBA")
        today_str = _today_date().isoformat()
        next_evt = SportsGetter._parse_event(
            _event_dict(
                date=today_str,
                time="23:00:00",
                home_id="134884",
                away_id="99999",
                home_team="Washington Wizards",
                away_team="Boston Celtics",
            ),
            "134884",
        )
        cutoff = datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(hours=48)
        _, _, today_rows = getter._build_rows([team], [next_evt], [None], cutoff)
        assert "Wizards" in today_rows
        assert "Celtics" in today_rows

    def test_old_result_excluded_from_recent(
        self, store: DataStore, default_cfg: _SportsCfg
    ) -> None:
        """Results older than recent_window_hours should not appear in recent_rows."""
        getter = self._getter(store, default_cfg)
        team = _resolved_team("Los Angeles Lakers", "134867", "NBA")
        # A game played 10 days ago
        old_date = (_today_date() - datetime.timedelta(days=10)).isoformat()
        last_evt = _EventSummary(
            team_id="134867",
            date_str=old_date,
            time_str="23:30:00",
            opponent="Utah Jazz",
            home_away="Home",
            home_score=90,
            away_score=95,
            status="FT",
            is_home=True,
        )
        # Only 48-hour window
        cutoff = datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(hours=48)
        _, recent, _ = getter._build_rows([team], [None], [last_evt], cutoff)
        assert recent == ""
