"""TheSportsDB sports scores and schedule integration.

Polls TheSportsDB v1 API for followed teams' upcoming schedules and recent
results, publishing flattened ``sports.*`` keys for use in dashboard templates.

Free API key ``123`` is used by default — no registration required.  A premium
key (see https://www.thesportsdb.com/pricing) unlocks more event history and
higher rate limits.

API Reference: https://www.thesportsdb.com/documentation

Store keys written:
    - ``sports.reachable``            — 1 when the API is accessible, 0 otherwise
    - ``sports.followed_count``       — number of configured teams
    - ``sports.today_count``          — games scheduled for today
    - ``sports.upcoming_rows``        — newline-delimited table rows for upcoming games
    - ``sports.recent_rows``          — newline-delimited table rows for recent results
    - ``sports.today_rows``           — newline-delimited table rows for today only
    - ``sports.team_N.name``          — full team name (e.g. ``"Los Angeles Lakers"``)
    - ``sports.team_N.short_name``    — last word(s) of team name (e.g. ``"Lakers"``)
    - ``sports.team_N.sport``         — sport label (e.g. ``"NBA"``)
    - ``sports.team_N.league``        — league name
    - ``sports.team_N.next.date``     — next-game date label (``"Today"``, ``"Tomorrow"``,
      ``"Apr 19"``, or empty string)
    - ``sports.team_N.next.days_until`` — days until next game (0 = today, -1 = unknown)
    - ``sports.team_N.next.time``     — next-game time in UTC (e.g. ``"7:30 PM"``)
    - ``sports.team_N.next.opponent`` — next opponent name
    - ``sports.team_N.next.home_away`` — ``"Home"`` or ``"Away"``
    - ``sports.team_N.last.date``     — most-recent-game date label
    - ``sports.team_N.last.opponent`` — most-recent opponent name
    - ``sports.team_N.last.home_away`` — ``"Home"`` or ``"Away"``
    - ``sports.team_N.last.score``    — score string (e.g. ``"131-107"``)
    - ``sports.team_N.last.result``   — ``"W"``, ``"L"``, ``"D"``, or ``""``

Row formats (pipe-delimited, for ``table`` widget):
    - ``upcoming_rows``: ``TEAM (SPORT)|DATE TIME vs OPPONENT (H/A)``
    - ``recent_rows``:   ``TEAM (SPORT)|W/L/D SCORE vs OPPONENT``
    - ``today_rows``:    ``TEAM vs OPPONENT|TIME (H/A)``
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
import datetime
import json
import logging
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from casedd.config import SportsTeamConfig
from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)

_BASE_URL = "https://www.thesportsdb.com/api/v1/json"

# Maximum per-team slot index; keeps the store bounded.
_MAX_TEAM_SLOTS: int = 50

# Finished-game status codes from TheSportsDB.
_FINISHED_STATUSES: frozenset[str] = frozenset({"FT", "AP", "AET", "ABD"})


@dataclass(frozen=True)
class _SportsCfg:
    """Bundled immutable configuration for :class:`SportsGetter`.

    Attributes:
        api_key: TheSportsDB API key (``"123"`` for the free tier).
        timeout: HTTP request timeout in seconds.
        max_teams: Maximum team slot index to emit.
        recent_window_hours: Window in hours for showing recent results.
    """

    api_key: str = "123"
    timeout: float = 5.0
    max_teams: int = 10
    recent_window_hours: int = 48


@dataclass(frozen=True)
class _ResolvedTeam:
    """A configured team with its resolved TheSportsDB ID and metadata."""

    config_team: str
    config_sport: str
    team_id: str
    name: str
    short_name: str
    sport: str
    league: str


@dataclass(frozen=True)
class _EventSummary:
    """Normalized single-event data for one team."""

    team_id: str
    date_str: str          # "YYYY-MM-DD"
    time_str: str          # "HH:MM:SS" UTC or ""
    opponent: str
    home_away: str         # "Home" or "Away"
    home_score: int | None
    away_score: int | None
    status: str            # "NS" (not started), "FT" (full time), etc.
    is_home: bool


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


def _today_date() -> datetime.date:
    """Return today's date in UTC (avoids bare ``datetime.date.today()``).

    Returns:
        Today's date as a :class:`datetime.date` object.
    """
    return datetime.datetime.now(tz=datetime.UTC).date()


def _label_date(date_str: str) -> str:
    """Convert a ``YYYY-MM-DD`` string to a human-readable label.

    Uses a dispatch table to keep the return-count within Ruff PLR0911 limits.

    Args:
        date_str: Date string in ISO format.

    Returns:
        ``"Today"``, ``"Tomorrow"``, ``"Mon Apr 19"``, or empty string on
        parse failure.
    """
    if not date_str:
        return ""
    try:
        event_date = datetime.date.fromisoformat(date_str)
    except ValueError:
        return date_str
    delta = (event_date - _today_date()).days
    _special: dict[int, str] = {0: "Today", 1: "Tomorrow", -1: "Yesterday"}
    if delta in _special:
        return _special[delta]
    if -7 < delta < 7:
        return event_date.strftime("%a %b ") + str(event_date.day)
    return event_date.strftime("%b ") + str(event_date.day)


def _format_time_utc(time_str: str) -> str:
    """Format a ``HH:MM:SS`` UTC time string to ``h:MM AM/PM UTC``.

    Args:
        time_str: Raw time string from the TheSportsDB API.

    Returns:
        Formatted time like ``"7:30 PM UTC"`` or empty string on failure.
    """
    if not time_str:
        return ""
    parts = time_str.split(":")
    if len(parts) < 2:
        return ""
    try:
        h = int(parts[0])
        m = int(parts[1])
    except ValueError:
        return ""
    period = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {period} UTC"


def _parse_result(event: _EventSummary) -> str:
    """Determine win/loss/draw from the perspective of the followed team.

    Args:
        event: Normalised event summary for the followed team.

    Returns:
        ``"W"``, ``"L"``, ``"D"``, or ``""`` when the game is not complete.
    """
    if event.home_score is None or event.away_score is None:
        return ""
    if event.status not in _FINISHED_STATUSES:
        return ""
    if event.home_score == event.away_score:
        return "D"
    if event.is_home:
        return "W" if event.home_score > event.away_score else "L"
    return "W" if event.away_score > event.home_score else "L"


def _score_string(event: _EventSummary) -> str:
    """Format the score from the followed team's perspective.

    Returns the score as ``"OUR_SCORE-THEIR_SCORE"`` so wins always show the
    higher number first from the team's view.

    Args:
        event: Normalised event summary.

    Returns:
        Score string like ``"131-107"`` or empty string when unavailable.
    """
    if event.home_score is None or event.away_score is None:
        return ""
    our = event.home_score if event.is_home else event.away_score
    their = event.away_score if event.is_home else event.home_score
    return f"{our}-{their}"


def _days_until(date_str: str) -> int:
    """Number of days from today until the event date.

    Args:
        date_str: Date string in ``YYYY-MM-DD`` format.

    Returns:
        Days until the event (negative means past); ``-999`` on parse failure.
    """
    if not date_str:
        return -999
    try:
        return (datetime.date.fromisoformat(date_str) - _today_date()).days
    except ValueError:
        return -999


def _is_today(date_str: str) -> bool:
    """Return True when the date string represents today.

    Args:
        date_str: Date string in ``YYYY-MM-DD`` format.

    Returns:
        Whether the date is today.
    """
    if not date_str:
        return False
    try:
        return datetime.date.fromisoformat(date_str) == _today_date()
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Getter implementation
# ---------------------------------------------------------------------------


class SportsGetter(BaseGetter):
    """Getter for sports scores and schedules via TheSportsDB v1 API.

    Resolves configured team names to TheSportsDB IDs at startup, then polls
    for each team's next upcoming event and most recent completed event.

    When ``sports_enabled`` is ``False`` or ``followed_teams`` is empty this
    getter performs no work (returns an empty dict on every cycle).

    Args:
        store: Shared data store.
        teams: Followed team configurations from ``casedd.yaml``.
        cfg: Bundled getter configuration (api key, timeout, limits).
        interval: Poll interval in seconds.
    """

    def __init__(
        self,
        store: DataStore,
        *,
        teams: list[SportsTeamConfig],
        cfg: _SportsCfg,
        interval: float = 300.0,
    ) -> None:
        """Initialise the sports getter.

        Args:
            store: Shared data store instance.
            teams: Followed-team configs from ``casedd.yaml``.
            cfg: Bundled getter configuration.
            interval: Seconds between polls.
        """
        super().__init__(store, interval)
        self._teams = teams[: cfg.max_teams]
        self._cfg = cfg
        self._recent_window_secs = cfg.recent_window_hours * 3600
        # Cache of resolved teams; populated lazily on first fetch.
        self._resolved: list[_ResolvedTeam] = []
        self._ids_resolved = False

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    def _api_get(self, endpoint: str, params: dict[str, str]) -> Any:
        """Perform a synchronous GET against the TheSportsDB v1 API.

        Args:
            endpoint: PHP endpoint filename (e.g. ``"searchteams.php"``).
            params: Query parameters dict.

        Returns:
            Parsed JSON body (dict or list).

        Raises:
            RuntimeError: On HTTP error or network failure.
        """
        query = urlencode(params)
        url = f"{_BASE_URL}/{self._cfg.api_key}/{endpoint}?{query}"
        try:
            with urlopen(url, timeout=self._cfg.timeout) as resp:  # noqa: S310 -- URL is always thesportsdb.com
                return json.loads(resp.read().decode())
        except HTTPError as exc:
            raise RuntimeError(f"TheSportsDB HTTP {exc.code}: {endpoint}") from exc
        except URLError as exc:
            raise RuntimeError(f"TheSportsDB network error: {exc}") from exc

    # ------------------------------------------------------------------
    # Team ID resolution
    # ------------------------------------------------------------------

    def _search_teams(self, name: str) -> list[dict[str, Any]]:
        """Search TheSportsDB for teams matching a name string.

        Args:
            name: Team name to search for.

        Returns:
            List of raw team dicts (may be empty on no match or API failure).
        """
        try:
            data = self._api_get("searchteams.php", {"t": name})
            teams = data.get("teams") if isinstance(data, dict) else None
            return teams if isinstance(teams, list) else []
        except RuntimeError as exc:
            _log.warning("Sports: team search failed for %r: %s", name, exc)
            return []

    def _resolve_team_id(self, cfg: SportsTeamConfig) -> _ResolvedTeam | None:
        """Resolve a team config entry to a ``_ResolvedTeam`` with API ID.

        Uses the ``sport`` hint to disambiguate when multiple results are
        returned (e.g. ``"West Virginia"`` appears in both NCAA Football and
        NCAA Basketball).

        Args:
            cfg: Team configuration entry from ``casedd.yaml``.

        Returns:
            Resolved team or ``None`` when no matching team was found.
        """
        results = self._search_teams(cfg.team)
        if not results:
            _log.warning(
                "Sports: no TheSportsDB match for team %r (sport hint: %r). "
                "Check the team name matches TheSportsDB exactly.",
                cfg.team,
                cfg.sport,
            )
            return None

        match: dict[str, Any] | None = None
        if cfg.sport and len(results) > 1:
            hint = cfg.sport.lower()
            for r in results:
                sport_field = str(r.get("strSport", "") or "").lower()
                league_field = str(r.get("strLeague", "") or "").lower()
                if hint in sport_field or hint in league_field:
                    match = r
                    break
        if match is None:
            match = results[0]

        team_id = str(match.get("idTeam", ""))
        name = str(match.get("strTeam", cfg.team))
        # Derive a short name: last word(s) of the team name, or the full name
        # when it is a single word (e.g. "Arsenal").
        words = name.split()
        short_name = words[-1] if len(words) > 1 else name
        sport = str(match.get("strSport", cfg.sport))
        league = str(match.get("strLeague", ""))
        _log.info(
            "Sports: resolved %r → %r (id=%s, sport=%s)",
            cfg.team,
            name,
            team_id,
            sport,
        )
        return _ResolvedTeam(
            config_team=cfg.team,
            config_sport=cfg.sport,
            team_id=team_id,
            name=name,
            short_name=short_name,
            sport=sport,
            league=league,
        )

    def _resolve_all(self) -> None:
        """Resolve all configured team names to TheSportsDB IDs.

        Logs warnings for any teams that cannot be found.  Sets
        ``self._ids_resolved = True`` even when some teams fail so the getter
        does not retry resolution on every poll cycle.
        """
        resolved: list[_ResolvedTeam] = []
        for cfg in self._teams:
            result = self._resolve_team_id(cfg)
            if result is not None:
                resolved.append(result)
        self._resolved = resolved
        self._ids_resolved = True
        _log.info(
            "Sports: resolved %d/%d team(s)",
            len(resolved),
            len(self._teams),
        )

    # ------------------------------------------------------------------
    # Event fetching and parsing
    # ------------------------------------------------------------------

    def _fetch_next_event(self, team: _ResolvedTeam) -> _EventSummary | None:
        """Fetch the next scheduled event for a team.

        Args:
            team: Resolved team with a valid TheSportsDB ID.

        Returns:
            Parsed event or ``None`` when no upcoming event is available.
        """
        try:
            data = self._api_get("eventsnext.php", {"id": team.team_id})
        except RuntimeError as exc:
            _log.debug("Sports: eventsnext failed for %s: %s", team.name, exc)
            return None
        events = data.get("events") if isinstance(data, dict) else None
        if not events or not isinstance(events, list):
            return None
        return self._parse_event(events[0], team.team_id)

    def _fetch_last_event(self, team: _ResolvedTeam) -> _EventSummary | None:
        """Fetch the most recent completed event for a team.

        Args:
            team: Resolved team with a valid TheSportsDB ID.

        Returns:
            Parsed event or ``None`` when no recent event is available.
        """
        try:
            data = self._api_get("eventslast.php", {"id": team.team_id})
        except RuntimeError as exc:
            _log.debug("Sports: eventslast failed for %s: %s", team.name, exc)
            return None
        # eventslast returns the results list under the key "results"
        events = data.get("results") if isinstance(data, dict) else None
        if not events or not isinstance(events, list):
            return None
        return self._parse_event(events[0], team.team_id)

    @staticmethod
    def _parse_event(raw: Any, our_team_id: str) -> _EventSummary | None:
        """Parse a raw TheSportsDB event dict into an ``_EventSummary``.

        Args:
            raw: Raw event dict from the TheSportsDB API.
            our_team_id: ID of the followed team to determine home/away.

        Returns:
            Parsed event or ``None`` on invalid data.
        """
        if not isinstance(raw, dict):
            return None
        home_id = str(raw.get("idHomeTeam", "") or "")
        is_home = home_id == our_team_id

        home_team = str(raw.get("strHomeTeam", "") or "")
        away_team = str(raw.get("strAwayTeam", "") or "")
        opponent = away_team if is_home else home_team

        date_str = str(raw.get("dateEvent", "") or "")
        time_str = str(raw.get("strTime", "") or "")
        status = str(raw.get("strStatus", "") or "")

        # Scores may be None when the game hasn't started.
        raw_home = raw.get("intHomeScore")
        raw_away = raw.get("intAwayScore")
        home_score: int | None = None
        away_score: int | None = None
        if raw_home is not None:
            with contextlib.suppress(TypeError, ValueError):
                home_score = int(raw_home)
        if raw_away is not None:
            with contextlib.suppress(TypeError, ValueError):
                away_score = int(raw_away)

        return _EventSummary(
            team_id=our_team_id,
            date_str=date_str,
            time_str=time_str,
            opponent=opponent,
            home_away="Home" if is_home else "Away",
            home_score=home_score,
            away_score=away_score,
            status=status,
            is_home=is_home,
        )

    # ------------------------------------------------------------------
    # Store key assembly
    # ------------------------------------------------------------------

    def _emit_team_slot(
        self,
        updates: dict[str, StoreValue],
        idx: int,
        team: _ResolvedTeam,
        next_evt: _EventSummary | None,
        last_evt: _EventSummary | None,
    ) -> None:
        """Write all ``sports.team_N.*`` keys for one team.

        Args:
            updates: Mutable dict of store updates being assembled.
            idx: 1-based team slot index.
            team: Resolved team metadata.
            next_evt: Next upcoming event, or ``None``.
            last_evt: Most recent completed event, or ``None``.
        """
        pfx = f"sports.team_{idx}"
        updates[f"{pfx}.name"] = team.name
        updates[f"{pfx}.short_name"] = team.short_name
        updates[f"{pfx}.sport"] = team.sport
        updates[f"{pfx}.league"] = team.league

        # Next-game keys
        if next_evt is not None:
            updates[f"{pfx}.next.date"] = _label_date(next_evt.date_str)
            updates[f"{pfx}.next.days_until"] = float(_days_until(next_evt.date_str))
            updates[f"{pfx}.next.time"] = _format_time_utc(next_evt.time_str)
            updates[f"{pfx}.next.opponent"] = next_evt.opponent
            updates[f"{pfx}.next.home_away"] = next_evt.home_away
        else:
            updates[f"{pfx}.next.date"] = ""
            updates[f"{pfx}.next.days_until"] = float(-999)
            updates[f"{pfx}.next.time"] = ""
            updates[f"{pfx}.next.opponent"] = ""
            updates[f"{pfx}.next.home_away"] = ""

        # Last-result keys
        if last_evt is not None:
            result = _parse_result(last_evt)
            score = _score_string(last_evt)
            updates[f"{pfx}.last.date"] = _label_date(last_evt.date_str)
            updates[f"{pfx}.last.opponent"] = last_evt.opponent
            updates[f"{pfx}.last.home_away"] = last_evt.home_away
            updates[f"{pfx}.last.score"] = score
            updates[f"{pfx}.last.result"] = result
        else:
            updates[f"{pfx}.last.date"] = ""
            updates[f"{pfx}.last.opponent"] = ""
            updates[f"{pfx}.last.home_away"] = ""
            updates[f"{pfx}.last.score"] = ""
            updates[f"{pfx}.last.result"] = ""

    def _build_rows(
        self,
        teams: list[_ResolvedTeam],
        next_events: list[_EventSummary | None],
        last_events: list[_EventSummary | None],
        recent_cutoff: datetime.datetime,
    ) -> tuple[str, str, str]:
        """Build the three row strings for table widgets.

        Args:
            teams: Resolved team list.
            next_events: Per-team next events (parallel with ``teams``).
            last_events: Per-team last events (parallel with ``teams``).
            recent_cutoff: Only include last events newer than this datetime.

        Returns:
            Tuple of ``(upcoming_rows, recent_rows, today_rows)`` strings.
        """
        upcoming: list[str] = []
        recent: list[str] = []
        today: list[str] = []

        for team, next_evt, last_evt in zip(teams, next_events, last_events, strict=False):
            label = f"{team.short_name} ({team.sport})"

            # Upcoming row
            if next_evt is not None and next_evt.date_str:
                date_lbl = _label_date(next_evt.date_str)
                time_lbl = _format_time_utc(next_evt.time_str)
                ha = next_evt.home_away[0]  # "H" or "A"
                parts = [date_lbl]
                if time_lbl:
                    parts.append(time_lbl)
                right = f"{'  '.join(parts)} vs {next_evt.opponent} ({ha})"
                upcoming.append(f"{label}|{right}")

                # Today's game row
                if _is_today(next_evt.date_str):
                    time_part = time_lbl or "TBD"
                    matchup = f"{team.short_name} vs {next_evt.opponent}"
                    today.append(f"{matchup}|{time_part} ({ha})")

            # Recent result row (only within the recency window)
            if last_evt is not None and last_evt.date_str:
                evt_date: datetime.datetime | None = None
                with contextlib.suppress(ValueError):
                    evt_date = datetime.datetime.fromisoformat(
                        f"{last_evt.date_str}T{last_evt.time_str or '00:00:00'}"
                    ).replace(tzinfo=datetime.UTC)

                # Include when parse failed (unknown time → assume recent)
                if evt_date is not None and evt_date < recent_cutoff:
                    continue

                result = _parse_result(last_evt)
                score = _score_string(last_evt)
                result_part = f"{result} {score}".strip() if result else score
                right = (
                    f"{result_part} vs {last_evt.opponent}"
                    if result_part
                    else f"vs {last_evt.opponent}"
                )
                recent.append(f"{label}|{right}")

        return "\n".join(upcoming), "\n".join(recent), "\n".join(today)

    # ------------------------------------------------------------------
    # BaseGetter.fetch implementation
    # ------------------------------------------------------------------

    async def fetch(self) -> dict[str, StoreValue]:
        """Poll TheSportsDB for all followed teams and return store updates.

        Returns:
            Mapping of ``sports.*`` store keys to current values, or an empty
            dict when no teams are configured.
        """
        if not self._teams:
            return {}

        # Resolve team IDs lazily on the first fetch (blocking I/O via
        # to_thread to avoid holding the event loop).
        if not self._ids_resolved:
            await asyncio.to_thread(self._resolve_all)

        if not self._resolved:
            # All resolutions failed — still mark reachable=0 and return.
            return {"sports.reachable": 0, "sports.followed_count": 0}

        # Fetch next and last events for every resolved team in parallel.
        fetch_tasks: list[Any] = []
        for team in self._resolved:
            fetch_tasks.append(asyncio.to_thread(self._fetch_next_event, team))
            fetch_tasks.append(asyncio.to_thread(self._fetch_last_event, team))

        raw_results: list[Any] = await asyncio.gather(*fetch_tasks, return_exceptions=True)

        # De-interleave: even indices = next events, odd = last events.
        next_events: list[_EventSummary | None] = []
        last_events: list[_EventSummary | None] = []
        for i, res in enumerate(raw_results):
            evt = res if isinstance(res, _EventSummary) else None
            if i % 2 == 0:
                next_events.append(evt)
            else:
                last_events.append(evt)

        # Calculate today-count and recency cutoff.
        today_count = sum(
            1 for e in next_events if e is not None and _is_today(e.date_str)
        )
        now_utc = datetime.datetime.now(tz=datetime.UTC)
        recent_cutoff = now_utc - datetime.timedelta(seconds=self._recent_window_secs)

        updates: dict[str, StoreValue] = {
            "sports.reachable": 1,
            "sports.followed_count": len(self._resolved),
            "sports.today_count": today_count,
        }

        # Clear stale per-team slots beyond the current team count.
        for idx in range(len(self._resolved) + 1, self._cfg.max_teams + 1):
            pfx = f"sports.team_{idx}"
            for suffix in (
                ".name", ".short_name", ".sport", ".league",
                ".next.date", ".next.days_until", ".next.time",
                ".next.opponent", ".next.home_away",
                ".last.date", ".last.opponent", ".last.home_away",
                ".last.score", ".last.result",
            ):
                updates[f"{pfx}{suffix}"] = ""

        for idx, (team, next_evt, last_evt) in enumerate(
            zip(self._resolved, next_events, last_events, strict=False), start=1
        ):
            self._emit_team_slot(updates, idx, team, next_evt, last_evt)

        upcoming_rows, recent_rows, today_rows = self._build_rows(
            self._resolved, next_events, last_events, recent_cutoff
        )
        updates["sports.upcoming_rows"] = upcoming_rows
        updates["sports.recent_rows"] = recent_rows
        updates["sports.today_rows"] = today_rows

        return updates
