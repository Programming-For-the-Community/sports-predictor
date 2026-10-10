"""
Player-row columns built from team totals of other players' rows: what the
opponent usually allows in each prop stat (opp_allowed_<stat>) and the
player's share of team volume (share_<stat>). Training and live serving
compute them through the same matchup_columns, each with its own lookup.
"""
import bisect
import time
from collections import defaultdict
from typing import Protocol

FOOTBALL_PROP_STATS = (
    "passing_yards", "passing_touchdowns", "rushing_yards", "rushing_touchdowns",
    "receiving_yards", "receiving_touchdowns", "defensive_sacks",
)
BASKETBALL_PROP_STATS = ("points", "rebounds", "assists", "steals", "blocks", "three_pointers_made")
BASKETBALL_USAGE_STATS = ("field_goal_attempts", "minutes")
HOCKEY_PROP_STATS = ("shots_total", "points", "goals", "assists", "hits", "blocked_shots")
HOCKEY_USAGE_STATS = ("time_on_ice_seconds", "power_play_time_on_ice_seconds", "shots_total")

# sport -> (prop stats, usage volume stats)
MATCHUP_STATS = {
    "nfl": (FOOTBALL_PROP_STATS, ("passing_attempts", "rushing_attempts", "receiving_targets")),
    "ncaafb": (FOOTBALL_PROP_STATS, ("passing_attempts", "rushing_attempts", "receiving_receptions")),
    "nba": (BASKETBALL_PROP_STATS, BASKETBALL_USAGE_STATS),
    "ncaambb": (BASKETBALL_PROP_STATS, BASKETBALL_USAGE_STATS),
    "nhl": (HOCKEY_PROP_STATS, HOCKEY_USAGE_STATS),
}

_EVENT_ROWS_TTL_SECONDS = 300
_EVENT_ROWS_MAX = 512


class MatchupLookup(Protocol):
    def team_games_before(self, team_id: str, before_date: str, limit: int) -> list[dict]:
        """The team's completed events before before_date, most recent first."""

    def team_totals(self, event_key: str, team_id: str) -> dict[str, float]:
        """Each stat summed over the team's player rows in that event."""


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _sum_by_team(rows: list[dict]) -> dict[str, dict[str, float]]:
    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        for stat, value in (row.get("stat_line") or {}).items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                totals[row.get("team_id")][stat] += value
    return totals


def opponent_in(event: dict, team_id: str) -> str | None:
    return next((p.get("entity_id") for p in event.get("participants", []) if p.get("entity_id") != team_id), None)


def _allowed_by_opponent(opponent_id: str | None, before_date: str, lookup: MatchupLookup, window: int) -> list[dict]:
    """Totals of whoever faced the opponent in each of its last `window` games."""
    if opponent_id is None:
        return []
    allowed = []
    for game in lookup.team_games_before(opponent_id, before_date, window):
        faced_by = opponent_in(game, opponent_id)
        if faced_by is not None and game.get("event_key") is not None:
            allowed.append(lookup.team_totals(game["event_key"], faced_by))
    return allowed


def _share(game: dict, stat: str, lookup: MatchupLookup) -> float | None:
    if game.get("event_key") is None:
        return None
    team_total = lookup.team_totals(game["event_key"], game.get("team_id")).get(stat)
    own = (game.get("stat_line") or {}).get(stat)
    return own / team_total if team_total and isinstance(own, (int, float)) else None


def matchup_columns(
    sport: str, player_game: dict, opponent_id: str | None, prior_games: list[dict], lookup: MatchupLookup, window: int,
) -> dict:
    """opp_allowed_<stat>: the opponent's last `window` games' average of what
    its opponents totaled; share_<stat>: the player's average share of team
    volume over prior_games. Empty for a sport without MATCHUP_STATS."""
    if sport not in MATCHUP_STATS:
        return {}
    prop_stats, usage_stats = MATCHUP_STATS[sport]
    allowed = [totals for totals in _allowed_by_opponent(opponent_id, player_game["event_date"], lookup, window) if totals]
    columns = {f"opp_allowed_{stat}": _mean([totals.get(stat, 0.0) for totals in allowed]) for stat in prop_stats}
    for stat in usage_stats:
        columns[f"share_{stat}"] = _mean([share for game in prior_games[:window] if (share := _share(game, stat, lookup)) is not None])
    return columns


class InMemoryMatchupLookup:
    """Training: every event and player row of the sport, already loaded."""

    def __init__(self, events: list[dict], player_games: list[dict]):
        self._totals = {}
        by_event: dict[str, list[dict]] = defaultdict(list)
        for row in player_games:
            by_event[row["event_key"]].append(row)
        for event_key, rows in by_event.items():
            for team_id, totals in _sum_by_team(rows).items():
                self._totals[(event_key, team_id)] = dict(totals)
        self._games: dict[str, list[dict]] = defaultdict(list)
        for event in sorted(events, key=lambda e: e["event_date"]):
            for participant in event.get("participants", []):
                self._games[participant.get("entity_id")].append(event)
        self._dates = {team_id: [e["event_date"] for e in games] for team_id, games in self._games.items()}

    def team_games_before(self, team_id: str, before_date: str, limit: int) -> list[dict]:
        end = bisect.bisect_left(self._dates.get(team_id, []), before_date)
        return self._games.get(team_id, [])[max(0, end - limit):end][::-1]

    def team_totals(self, event_key: str, team_id: str) -> dict[str, float]:
        return self._totals.get((event_key, team_id), {})


_event_rows_cache: dict[str, tuple[float, dict[str, dict[str, float]]]] = {}


class StorageMatchupLookup:
    """Live serving: reads each past game's player rows at most once per
    _EVENT_ROWS_TTL_SECONDS across requests in a warm container."""

    def __init__(self, storage, sport: str, events: list[dict] | None = None):
        self._storage = storage
        self._sport = sport
        self._events = events

    def team_games_before(self, team_id: str, before_date: str, limit: int) -> list[dict]:
        return self._storage.get_team_events(self._sport, team_id, before_date=before_date, limit=limit, events=self._events)

    def team_totals(self, event_key: str, team_id: str) -> dict[str, float]:
        cached = _event_rows_cache.get(event_key)
        if cached is None or time.monotonic() - cached[0] >= _EVENT_ROWS_TTL_SECONDS:
            if len(_event_rows_cache) >= _EVENT_ROWS_MAX:
                _event_rows_cache.pop(next(iter(_event_rows_cache)))
            totals = {team: dict(stats) for team, stats in _sum_by_team(self._storage.get_player_game_stats_for_event(event_key)).items()}
            cached = (time.monotonic(), totals)
            _event_rows_cache[event_key] = cached
        return cached[1].get(team_id, {})


def live_matchup_columns(
    storage, sport: str, player_game: dict, home_id: str, away_id: str, prior_games: list[dict], window: int,
    events: list[dict] | None = None,
) -> dict:
    """matchup_columns for a live (not yet played) player row."""
    opponent_id = away_id if player_game["team_id"] == home_id else home_id
    return matchup_columns(sport, player_game, opponent_id, prior_games, StorageMatchupLookup(storage, sport, events), window)
