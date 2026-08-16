"""ESPN live sports scoreboard getter for NBA and WNBA.

Polls the unofficial (but public) ESPN scoreboard API for NBA and WNBA
games, publishing per-game slot keys so templates can render live scores,
team logos, series standings, and countdown-to-tipoff.

ESPN API endpoints used:
    https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard
    https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard

These return the same JSON shape regardless of authentication — no API key
or account is required.  ESPN has not published rate-limit terms for this
endpoint; we apply a conservative adaptive strategy:
    - 30 s between polls when any game is actively in progress.
    - 300 s between polls when all games are final or the day has none.

Logo URLs from the API point to ESPN's CDN.  They are downloaded once per
team abbreviation and cached locally (bounded LRU, max 64 entries) to avoid
per-frame network I/O.  The local path is stored so the ``image`` widget
renders from disk.

Store keys written (``L`` = ``nba`` or ``wnba``, ``N`` = 0-based slot index):
    - ``espn_L.reachable``          — 1 when last poll succeeded, 0 otherwise
    - ``espn_L.live_count``         — number of games currently in progress
    - ``espn_L.game_count``         — total games found for today
    - ``espn_L.game_N.state``       — ``"pre"``, ``"in"``, or ``"post"``
    - ``espn_L.game_N.away_abbr``   — away team abbreviation (e.g. ``"NY"``)
    - ``espn_L.game_N.home_abbr``   — home team abbreviation
    - ``espn_L.game_N.away_name``   — away team display name
    - ``espn_L.game_N.home_name``   — home team display name
    - ``espn_L.game_N.away_score``  — away score as integer (0 pre-game)
    - ``espn_L.game_N.home_score``  — home score as integer (0 pre-game)
    - ``espn_L.game_N.away_logo``   — local path to cached logo PNG
    - ``espn_L.game_N.home_logo``   — local path to cached logo PNG
    - ``espn_L.game_N.away_record`` — season record (e.g. ``"53-29"``)
    - ``espn_L.game_N.home_record`` — season record
    - ``espn_L.game_N.period``      — display string (``"Q1"``, ``"HT"``,
      ``"OT"``, ``"Final"``, ``"Pre"`` …)
    - ``espn_L.game_N.clock``       — game clock (``"8:42"``) or empty
    - ``espn_L.game_N.detail``      — ESPN short detail (``"End of Q2"``)
    - ``espn_L.game_N.series``      — playoff series summary or ``""``
    - ``espn_L.game_N.tipoff``      — human-readable tipoff time (pre-game)
    - ``espn_L.game_N.mins_to_tip`` — minutes until tipoff (pre-game, -1 if
      tipoff time is unknown or game is not pre-game)
"""

from __future__ import annotations

import asyncio
import collections
from dataclasses import dataclass
import datetime
import json
import logging
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)

# Scoreboard endpoints — no auth required.
_ESPN_ENDPOINTS: dict[str, str] = {
    "nba": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "wnba": "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard",
}

# Conservative poll intervals (seconds).
_INTERVAL_LIVE: float = 30.0
_INTERVAL_IDLE: float = 300.0

# Maximum game slots emitted per league.
_MAX_GAME_SLOTS: int = 8

# Logo cache bounds.
_LOGO_CACHE_MAXSIZE: int = 64

# Default logo cache directory.
_DEFAULT_LOGO_DIR: str = "/tmp/casedd-espn-logos"  # noqa: S108 — non-sensitive cache


@dataclass(frozen=True)
class _TeamInfo:
    """Parsed team data from one competition competitor entry.

    Attributes:
        abbr: Short abbreviation, e.g. ``"NY"``.
        name: Full display name, e.g. ``"New York Knicks"``.
        score: Current score as integer.
        logo_url: ESPN CDN logo URL.
        record: Season record string, e.g. ``"53-29"``.
        home_away: ``"home"`` or ``"away"``.
    """

    abbr: str
    name: str
    score: int
    logo_url: str
    record: str
    home_away: str


@dataclass(frozen=True)
class _GameSnapshot:
    """Parsed snapshot of one ESPN competition entry.

    Attributes:
        state: ``"pre"``, ``"in"``, or ``"post"``.
        away: Away team info.
        home: Home team info.
        period: Display period string such as ``"Q3"`` or ``"HT"``.
        clock: Clock string such as ``"8:42"`` or ``""``.
        detail: ESPN short status detail.
        series: Playoff series summary or empty string.
        tipoff_utc: Tipoff datetime (UTC) for pre-game, else None.
    """

    state: str
    away: _TeamInfo
    home: _TeamInfo
    period: str
    clock: str
    detail: str
    series: str
    tipoff_utc: datetime.datetime | None


def _parse_team(competitor: dict[str, Any]) -> _TeamInfo:
    """Parse a single competitor dict from the ESPN competitions array.

    Args:
        competitor: Raw competitor dict from ESPN JSON.

    Returns:
        Populated :class:`_TeamInfo`.
    """
    team: dict[str, Any] = competitor.get("team") or {}
    records: list[dict[str, Any]] = competitor.get("records") or []
    record = ""
    for r in records:
        if r.get("type") == "total":
            record = str(r.get("summary") or "")
            break

    raw_score = competitor.get("score") or "0"
    try:
        score = int(raw_score)
    except (ValueError, TypeError):
        score = 0

    return _TeamInfo(
        abbr=str(team.get("abbreviation") or "???").upper(),
        name=str(team.get("displayName") or team.get("name") or "Unknown"),
        score=score,
        logo_url=str(team.get("logo") or ""),
        record=record,
        home_away=str(competitor.get("homeAway") or "home").lower(),
    )


def _parse_period(period: int, state: str, detail: str) -> str:  # noqa: PLR0911
    """Convert raw period/state into a human-readable period label.

    Args:
        period: Numeric period from ESPN (1-4, 5+ = OT).
        state: Game state string (``"pre"``, ``"in"``, ``"post"``).
        detail: ESPN status detail string.

    Returns:
        Display string such as ``"Q1"``, ``"HT"``, ``"OT"``, ``"Final"``.
    """
    if state == "pre":
        return "Pre"
    if state == "post":
        return "Final"
    detail_lower = detail.lower()
    if "halftime" in detail_lower or "half time" in detail_lower:
        return "HT"
    if "end of" in detail_lower:
        # e.g. "End of Q1" → "End Q1"
        return f"End {detail.rsplit(maxsplit=1)[-1]}"
    if period == 0:
        return "Pre"
    if period <= 4:
        return f"Q{period}"
    return f"OT{period - 4}" if period > 5 else "OT"


def _parse_series(competition: dict[str, Any]) -> str:
    """Extract playoff series summary if present.

    Args:
        competition: Raw competition dict from ESPN.

    Returns:
        Human-readable series summary, e.g. ``"NY leads 3-0"`` or ``""``.
    """
    series: dict[str, Any] = competition.get("series") or {}
    if not series:
        return ""
    summary: str = str(series.get("summary") or "")
    return summary


def _parse_game(event: dict[str, Any]) -> _GameSnapshot | None:
    """Parse a single ESPN event dict into a :class:`_GameSnapshot`.

    Args:
        event: Raw event from the ESPN scoreboard ``events`` array.

    Returns:
        Parsed snapshot or ``None`` if the event is malformed.
    """
    competitions: list[dict[str, Any]] = event.get("competitions") or []
    if not competitions:
        return None
    comp: dict[str, Any] = competitions[0]
    competitors: list[dict[str, Any]] = comp.get("competitors") or []
    if len(competitors) < 2:
        return None

    teams = [_parse_team(c) for c in competitors]
    away = next((t for t in teams if t.home_away == "away"), teams[0])
    home = next((t for t in teams if t.home_away == "home"), teams[-1])

    status: dict[str, Any] = comp.get("status") or {}
    status_type: dict[str, Any] = status.get("type") or {}
    state = str(status_type.get("state") or "pre").lower()
    # Normalize ESPN states to pre/in/post.
    if state not in {"pre", "in", "post"}:
        state = "post" if status_type.get("completed") else "pre"

    clock = str(status.get("displayClock") or "")
    period = int(status.get("period") or 0)
    detail = str(status_type.get("shortDetail") or status_type.get("detail") or "")

    # Tipoff time from the event's date field (ISO 8601 UTC).
    tipoff_utc: datetime.datetime | None = None
    if state == "pre":
        raw_date = str(event.get("date") or "")
        if raw_date:
            try:
                tipoff_utc = datetime.datetime.fromisoformat(
                    raw_date.rstrip("Z")
                ).replace(tzinfo=datetime.UTC)
            except ValueError:
                tipoff_utc = None

    return _GameSnapshot(
        state=state,
        away=away,
        home=home,
        period=_parse_period(period, state, detail),
        clock=clock if state == "in" else "",
        detail=detail,
        series=_parse_series(comp),
        tipoff_utc=tipoff_utc,
    )


def _mins_to_tip(tipoff_utc: datetime.datetime | None) -> int:
    """Compute minutes until tipoff from now (UTC).

    Args:
        tipoff_utc: Scheduled tipoff time in UTC, or ``None``.

    Returns:
        Minutes until tipoff (may be negative if already started), or
        ``-1`` if tipoff time is unknown.
    """
    if tipoff_utc is None:
        return -1
    now = datetime.datetime.now(tz=datetime.UTC)
    delta = tipoff_utc - now
    return max(-1, int(delta.total_seconds() // 60))


def _tipoff_label(tipoff_utc: datetime.datetime | None) -> str:
    """Format tipoff as a human-readable local-time label.

    Args:
        tipoff_utc: Tipoff datetime in UTC.

    Returns:
        String such as ``"7:30 PM ET"`` or ``""`` if unknown.
    """
    if tipoff_utc is None:
        return ""
    # Convert to US Eastern for display (common for NBA/WNBA broadcast times).
    et_offset = datetime.timezone(datetime.timedelta(hours=-4))  # EDT; close enough
    local = tipoff_utc.astimezone(et_offset)
    return local.strftime("%-I:%M %p ET").strip()


class EspnSportsGetter(BaseGetter):
    """Polls ESPN scoreboard API for NBA and WNBA live scores.

    Args:
        store: Shared data store.
        leagues: Sequence of league slugs to poll (``"nba"``, ``"wnba"``).
        logo_cache_dir: Directory for cached team logo PNGs.
        timeout: HTTP request timeout in seconds.
        user_agent: HTTP User-Agent string sent with all requests.
    """

    def __init__(
        self,
        store: DataStore,
        leagues: list[str] | None = None,
        logo_cache_dir: str = _DEFAULT_LOGO_DIR,
        timeout: float = 5.0,
        user_agent: str = "CASEDD/0.2 (github.com/mdmoore25404/casedd)",
    ) -> None:
        """Initialise ESPN sports getter.

        Args:
            store: Shared data store.
            leagues: League slugs to track; defaults to ``["nba", "wnba"]``.
            logo_cache_dir: Directory for cached team logo PNGs.
            timeout: HTTP request timeout in seconds.
            user_agent: HTTP User-Agent header value.
        """
        super().__init__(store, _INTERVAL_IDLE)
        self._leagues: list[str] = [
            lg.lower()
            for lg in (leagues if leagues is not None else ["nba", "wnba"])
            if lg.lower() in _ESPN_ENDPOINTS
        ]
        self._logo_dir = Path(logo_cache_dir)
        self._timeout = timeout
        self._user_agent = user_agent
        # Bounded LRU logo cache: abbr → local Path.
        self._logo_cache: collections.OrderedDict[str, Path] = collections.OrderedDict()

    async def fetch(self) -> dict[str, StoreValue]:
        """Poll all configured leagues and return merged store updates."""
        result = await asyncio.to_thread(self._sample_all)
        # Adapt poll interval based on live game count.
        live = sum(
            int(result.get(f"espn_{lg}.live_count") or 0) for lg in self._leagues
        )
        self._interval = _INTERVAL_LIVE if live > 0 else _INTERVAL_IDLE
        return result

    def _sample_all(self) -> dict[str, StoreValue]:
        """Fetch all leagues synchronously and merge results."""
        out: dict[str, StoreValue] = {}
        for league in self._leagues:
            out.update(self._sample_league(league))
        return out

    def _sample_league(self, league: str) -> dict[str, StoreValue]:
        """Fetch and parse one league's scoreboard.

        Args:
            league: League slug (``"nba"`` or ``"wnba"``).

        Returns:
            Flat dict of store keys for this league.
        """
        prefix = f"espn_{league}"
        url = _ESPN_ENDPOINTS[league]
        try:
            req = Request(url, headers={"User-Agent": self._user_agent})  # noqa: S310 — HTTPS-only ESPN endpoint
            with urlopen(req, timeout=self._timeout) as resp:  # noqa: S310 — HTTPS-only ESPN endpoint
                raw: dict[str, Any] = json.loads(resp.read())
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            _log.warning("espn_sports: %s fetch failed: %s", league.upper(), exc)
            return {f"{prefix}.reachable": 0, f"{prefix}.live_count": 0, f"{prefix}.game_count": 0}

        events: list[dict[str, Any]] = raw.get("events") or []
        games: list[_GameSnapshot] = []
        for ev in events:
            snap = _parse_game(ev)
            if snap is not None:
                games.append(snap)

        games = games[:_MAX_GAME_SLOTS]
        live_count = sum(1 for g in games if g.state == "in")

        out: dict[str, StoreValue] = {
            f"{prefix}.reachable": 1,
            f"{prefix}.live_count": live_count,
            f"{prefix}.game_count": len(games),
        }

        for idx, game in enumerate(games):
            out.update(self._game_to_keys(f"{prefix}.game_{idx}", game))

        # Zero-out any stale slots beyond today's game count.
        for idx in range(len(games), _MAX_GAME_SLOTS):
            out.update(self._empty_slot(f"{prefix}.game_{idx}"))

        return out

    def _game_to_keys(self, slot: str, game: _GameSnapshot) -> dict[str, StoreValue]:
        """Convert a :class:`_GameSnapshot` to flat store key/value pairs.

        Args:
            slot: Store key prefix, e.g. ``"espn_nba.game_0"``.
            game: Parsed game snapshot.

        Returns:
            Flat dict of store entries for this game slot.
        """
        away_logo = self._resolve_logo(game.away.abbr, game.away.logo_url)
        home_logo = self._resolve_logo(game.home.abbr, game.home.logo_url)
        mtip = _mins_to_tip(game.tipoff_utc) if game.state == "pre" else -1

        return {
            f"{slot}.state": game.state,
            f"{slot}.away_abbr": game.away.abbr,
            f"{slot}.home_abbr": game.home.abbr,
            f"{slot}.away_name": game.away.name,
            f"{slot}.home_name": game.home.name,
            f"{slot}.away_score": float(game.away.score),
            f"{slot}.home_score": float(game.home.score),
            f"{slot}.away_logo": str(away_logo) if away_logo else "",
            f"{slot}.home_logo": str(home_logo) if home_logo else "",
            f"{slot}.away_record": game.away.record,
            f"{slot}.home_record": game.home.record,
            f"{slot}.period": game.period,
            f"{slot}.clock": game.clock,
            f"{slot}.detail": game.detail,
            f"{slot}.series": game.series,
            f"{slot}.tipoff": _tipoff_label(game.tipoff_utc) if game.state == "pre" else "",
            f"{slot}.mins_to_tip": float(mtip),
        }

    def _empty_slot(self, slot: str) -> dict[str, StoreValue]:
        """Emit cleared/zero values for an unused game slot.

        Args:
            slot: Store key prefix.

        Returns:
            Flat dict with zeroed/empty values.
        """
        return {
            f"{slot}.state": "",
            f"{slot}.away_abbr": "",
            f"{slot}.home_abbr": "",
            f"{slot}.away_name": "",
            f"{slot}.home_name": "",
            f"{slot}.away_score": 0.0,
            f"{slot}.home_score": 0.0,
            f"{slot}.away_logo": "",
            f"{slot}.home_logo": "",
            f"{slot}.away_record": "",
            f"{slot}.home_record": "",
            f"{slot}.period": "",
            f"{slot}.clock": "",
            f"{slot}.detail": "",
            f"{slot}.series": "",
            f"{slot}.tipoff": "",
            f"{slot}.mins_to_tip": -1.0,
        }

    def _resolve_logo(self, abbr: str, url: str) -> Path | None:
        """Return a local cached path for the team logo, downloading if needed.

        Uses two bounded LRU caches — one for each URL source type — so that
        URL-keyed remote lookups and abbr-keyed local hits both stay bounded.

        Args:
            abbr: Team abbreviation used as the filename stem.
            url: ESPN CDN logo URL.

        Returns:
            Local :class:`~pathlib.Path` to the cached PNG, or ``None`` on error.
        """
        if not url:
            return None

        # Check abbr → local path cache first.
        if abbr in self._logo_cache:
            self._logo_cache.move_to_end(abbr)
            cached = self._logo_cache[abbr]
            if cached.exists():
                return cached
            # File disappeared — evict and re-download.
            del self._logo_cache[abbr]

        # Download and cache.
        self._logo_dir.mkdir(parents=True, exist_ok=True)
        dest = self._logo_dir / f"{abbr.lower()}.png"
        try:
            req = Request(url, headers={"User-Agent": self._user_agent})  # noqa: S310 — HTTPS-only ESPN CDN
            with urlopen(req, timeout=self._timeout) as resp:  # noqa: S310 — HTTPS-only ESPN CDN
                dest.write_bytes(resp.read())
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            _log.debug("espn_sports: logo download failed for %s: %s", abbr, exc)
            return None

        self._logo_cache[abbr] = dest
        self._logo_cache.move_to_end(abbr)
        if len(self._logo_cache) > _LOGO_CACHE_MAXSIZE:
            self._logo_cache.popitem(last=False)

        return dest


