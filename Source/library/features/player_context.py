"""
Player-row columns about the game around a player, built through a
MatchupLookup (library.features.matchup) so training and serving read the
same rows:

- own_injury_status: the player's 0-3 status on the event's pre-game
  injury report (None without one -- see library.normalize.pregame).
- teammates_out_<stat>_share: summed over teammates listed Out, Doubtful
  or on injured reserve, each one's mean share of team volume over their
  last RECENT_GAMES games. team_qb_injury_status (NFL): the status of an
  injured teammate who has thrown at least half the team's passes lately,
  else 0.
- Basketball: projected_pace (mean of both teams' recent possessions per
  game), opp_def_rating and opp_efg_allowed (what the opponent's recent
  opponents managed against it).
- Football: opp_yards_per_pass_allowed, opp_yards_per_rush_allowed and
  opp_sack_rate (the opponent's sacks per dropback faced).
- minutes_trend (basketball): mean minutes over the last 3 games minus
  the last RECENT_GAMES; starts_rate_l10: share of the last RECENT_GAMES
  games started.

Team figures are sums of the player rows of each game (team turnovers
aren't in player rows).
"""
from library.features.common import _injury_status_ordinal
from library.normalize.pregame import pregame_injuries

RECENT_GAMES = 10
TREND_GAMES = 3
OUT_STATUSES = frozenset({"Out", "Doubtful", "Injured Reserve"})
QB_SHARE_THRESHOLD = 0.5

_SHARE_COLUMNS = {
    "nba": {"minutes": "teammates_out_minutes_share", "field_goal_attempts": "teammates_out_fga_share"},
    "ncaambb": {"minutes": "teammates_out_minutes_share", "field_goal_attempts": "teammates_out_fga_share"},
    "nfl": {"receiving_targets": "teammates_out_target_share", "rushing_attempts": "teammates_out_carry_share"},
}
_BASKETBALL = {"nba", "ncaambb"}
_FOOTBALL = {"nfl", "ncaafb"}


def _side_injuries(event: dict, team_id: str) -> list[dict] | None:
    for participant in event.get("participants", []):
        if participant.get("entity_id") == team_id and participant.get("role") in ("home", "away"):
            return pregame_injuries(event, participant["role"])
    return None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _share(game: dict, stat: str, lookup) -> float | None:
    own = (game.get("stat_line") or {}).get(stat)
    total = lookup.team_totals(game.get("event_key"), game.get("team_id")).get(stat) if game.get("event_key") else None
    return own / total if total and isinstance(own, (int, float)) else None


def _recent_share(entity_id: str, stat: str, before_date: str, lookup) -> float | None:
    games = lookup.player_games_before(entity_id, before_date, RECENT_GAMES)
    return _mean([share for game in games if (share := _share(game, stat, lookup)) is not None])


def _teammates_out_share(out: list[dict], stat: str, before_date: str, lookup) -> float:
    return sum(share for i in out if (share := _recent_share(i["entity_id"], stat, before_date, lookup)) is not None)


def _team_qb_status(injuries: list[dict], teammates: list[dict], before_date: str, lookup) -> int:
    """Status of an injured teammate who has thrown at least half the team's recent passes, else 0."""
    return max(
        (_injury_status_ordinal(injuries, i["entity_id"]) for i in teammates
         if (_recent_share(i["entity_id"], "passing_attempts", before_date, lookup) or 0) >= QB_SHARE_THRESHOLD),
        default=0,
    )


def injury_columns(sport: str, player_game: dict, event: dict, lookup) -> dict:
    injuries = _side_injuries(event, player_game["team_id"])
    columns = {"own_injury_status": _injury_status_ordinal(injuries, player_game["entity_id"])}
    share_columns = _SHARE_COLUMNS.get(sport, {})
    names = [*share_columns.values(), *(["team_qb_injury_status"] if sport == "nfl" else [])]
    if injuries is None:
        return {**columns, **dict.fromkeys(names)}

    before_date = player_game["event_date"]
    teammates = [i for i in injuries if i.get("entity_id") != player_game["entity_id"]]
    out = [i for i in teammates if i.get("status") in OUT_STATUSES]
    for stat, name in share_columns.items():
        columns[name] = _teammates_out_share(out, stat, before_date, lookup)
    if sport == "nfl":
        columns["team_qb_injury_status"] = _team_qb_status(injuries, teammates, before_date, lookup)
    return columns


def _other_team(game: dict, team_id: str) -> str | None:
    return next((p.get("entity_id") for p in game.get("participants", []) if p.get("entity_id") != team_id), None)


def _team_games(team_id: str | None, before_date: str, lookup) -> list[tuple[dict, dict]]:
    """(team's totals, its opponent's totals) for the team's recent games."""
    if team_id is None:
        return []
    games = []
    for game in lookup.team_games_before(team_id, before_date, RECENT_GAMES):
        own = lookup.team_totals(game.get("event_key"), team_id)
        opponent = lookup.team_totals(game.get("event_key"), _other_team(game, team_id))
        if own and opponent:
            games.append((own, opponent))
    return games


def _ratio(pairs: list[tuple[float, float]], scale: float = 1.0) -> float | None:
    den = sum(b for _, b in pairs)
    return scale * sum(a for a, _ in pairs) / den if den else None


def _possessions(totals: dict) -> float:
    return (
        totals.get("field_goal_attempts", 0.0) - totals.get("offensive_rebounds", 0.0)
        + totals.get("turnovers", 0.0) + 0.44 * totals.get("free_throw_attempts", 0.0)
    )


def _game_possessions(own: dict, opponent: dict) -> float:
    return (_possessions(own) + _possessions(opponent)) / 2


def basketball_columns(player_game: dict, opponent_id: str | None, lookup) -> dict:
    before_date = player_game["event_date"]
    own_games, opponent_games = _team_games(player_game["team_id"], before_date, lookup), _team_games(opponent_id, before_date, lookup)
    paces = [_mean([_game_possessions(a, b) for a, b in games]) for games in (own_games, opponent_games)]
    faced = [(b, _game_possessions(a, b)) for a, b in opponent_games]  # opponent's opponents' totals
    return {
        "projected_pace": _mean([p for p in paces if p is not None]) if all(p is not None for p in paces) else None,
        "opp_def_rating": _ratio([(t.get("points", 0.0), poss) for t, poss in faced], 100.0),
        "opp_efg_allowed": _ratio([
            (t.get("field_goals_made", 0.0) + 0.5 * t.get("three_pointers_made", 0.0), t.get("field_goal_attempts", 0.0))
            for t, _ in faced
        ]),
    }


def football_columns(player_game: dict, opponent_id: str | None, lookup) -> dict:
    games = _team_games(opponent_id, player_game["event_date"], lookup)  # (opponent's totals, its opponents' totals)
    return {
        "opp_yards_per_pass_allowed": _ratio([(b.get("passing_yards", 0.0), b.get("passing_attempts", 0.0)) for _, b in games]),
        "opp_yards_per_rush_allowed": _ratio([(b.get("rushing_yards", 0.0), b.get("rushing_attempts", 0.0)) for _, b in games]),
        "opp_sack_rate": _ratio([
            (a.get("defensive_sacks", 0.0), b.get("passing_attempts", 0.0) + a.get("defensive_sacks", 0.0)) for a, b in games
        ]),
    }


def trend_columns(sport: str, player_game: dict, lookup) -> dict:
    games = lookup.player_games_before(player_game["entity_id"], player_game["event_date"], RECENT_GAMES)
    columns = {"starts_rate_l10": _mean([float(bool(g.get("started"))) for g in games])}
    if sport in _BASKETBALL:
        minutes = [m for g in games if isinstance(m := (g.get("stat_line") or {}).get("minutes"), (int, float))]
        recent, overall = _mean(minutes[:TREND_GAMES]), _mean(minutes)
        columns["minutes_trend"] = recent - overall if recent is not None and overall is not None else None
    return columns


def context_columns(sport: str, player_game: dict, opponent_id: str | None, event: dict, lookup) -> dict:
    columns = {**injury_columns(sport, player_game, event, lookup), **trend_columns(sport, player_game, lookup)}
    if sport in _BASKETBALL:
        columns.update(basketball_columns(player_game, opponent_id, lookup))
    elif sport in _FOOTBALL:
        columns.update(football_columns(player_game, opponent_id, lookup))
    return columns
