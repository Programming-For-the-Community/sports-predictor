"""
Pure NHL feature-computation functions -- no AWS calls, every function
takes already-fetched rows and returns numbers, so training
(library.features.nhl_dataset) and serving share the same code.

Hockey is low-scoring and noisy, so these differ from the basketball
features in three ways:
- Shot volume (shots, shot attempts) carries most of the team signal,
  alongside the goal-based versions.
- Windows are longer (WINDOWS games, plus season to date).
- Percentages (shooting, save, power play, penalty kill) are shrunk
  toward the league mean by adding prior shots/opportunities.

Everything is computed from "records": one flat dict per team per game
(team_game_record) or per goalie per appearance (goalie_game_record),
passed most recent first and never including the game being scored.
"""
import re
from datetime import datetime

from library.features import geo, nhl_teams
from library.features.common import OFFSEASON_GAP_DAYS, rest_days, rolling_player_stat_averages

WINDOWS = (10, 25)
LONG_WINDOW = WINDOWS[-1]
EWM_HALF_LIFE_GAMES = 5
# Prior games an event builder receives per team: over one full season.
TEAM_HISTORY_GAMES = 110

# Elo constants fitted on 2015-16..2025-26 results.
ELO_K_FACTOR = 7.0
ELO_HOME_ADVANTAGE = 25.0
ELO_SEASON_CARRYOVER = 0.7

REGULATION_PERIODS = 3
REGULATION_SECONDS = 3600
PLAYOFF_SEASON_TYPE = 3
SERIES_WINS_NEEDED = 4

# League-average rates the shrunk percentages regress toward, and how many
# shots/opportunities of prior each carries.
LEAGUE_SAVE_PCT = 0.905
LEAGUE_SHOOTING_PCT = 1 - LEAGUE_SAVE_PCT
LEAGUE_POWER_PLAY_PCT = 0.20
TEAM_SHOT_PRIOR = 300
SPECIAL_TEAMS_PRIOR = 60
GOALIE_SHOT_PRIOR = 500
OVERTIME_GAMES_PRIOR = 10

PULLED_BEFORE_SECONDS = 50 * 60
BACKUP_START_SHARE = 0.4
MIN_TEAM_GAMES_FOR_START_SHARE = 5
MAX_DAYS_SINCE_LAST_START = 30

LINEUP_WINDOW_GAMES = 10
# The window a player's avg_<stat> columns cover -- the prop trainer's
# naive baseline and volume filter read those.
PLAYER_WINDOW = WINDOWS[0]
SKATER_HISTORY_GAMES = 82
SKATER_RECENT_GAMES = 3
SKATER_SHOT_PRIOR = 100
GOALIE_PROP_STATS = ("saves", "goals_against", "shots_against")
LINEUP_MIN_GAMES = 5
TOP_SCORER_COUNT = 3

TEAM_INJURY_COUNT_STATUSES = frozenset({"Out", "Injured Reserve", "Suspension"})

# Headline metrics that also get a home-minus-away column.
DIFF_METRICS = (
    f"goal_diff_last{LONG_WINDOW}", f"shot_share_last{LONG_WINDOW}", f"shot_attempt_share_last{LONG_WINDOW}",
    f"pdo_last{LONG_WINDOW}", f"net_special_teams_last{LONG_WINDOW}", "points_pct_season", "rest_days",
)


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _plus(*values):
    return None if any(v is None for v in values) else sum(values)


def _minus(a, b):
    return None if a is None or b is None else a - b


def _stat(line: dict, key: str):
    return _number(line.get(key))


def _mean(records: list[dict], field: str) -> float | None:
    values = [r[field] for r in records if r.get(field) is not None]
    return sum(values) / len(values) if values else None


def _ratio(records: list[dict], numerator: str, denominator: str, prior_rate: float = 0.0, prior_n: float = 0.0) -> float | None:
    """sum(numerator) / sum(denominator) over the records carrying both,
    regressed toward prior_rate by prior_n of the denominator. None with
    no qualifying record."""
    pairs = [(r[numerator], r[denominator]) for r in records if r.get(numerator) is not None and r.get(denominator) is not None]
    if not pairs:
        return None
    total = sum(d for _, d in pairs) + prior_n
    return (sum(n for n, _ in pairs) + prior_rate * prior_n) / total if total else None


def _share(records: list[dict], own: str, other: str) -> float | None:
    pairs = [(r[own], r[other]) for r in records if r.get(own) is not None and r.get(other) is not None]
    total = sum(a + b for a, b in pairs)
    return sum(a for a, _ in pairs) / total if total else None


def _ewm(values: list[float]) -> float | None:
    """Recency-weighted mean of values (most recent first)."""
    if not values:
        return None
    weights = [0.5 ** (age / EWM_HALF_LIFE_GAMES) for age in range(len(values))]
    return sum(w * v for w, v in zip(weights, values)) / sum(weights)


# ── Team game records ───────────────────────────────────────────────────────

def _side(event: dict, team_id: str) -> tuple[dict | None, dict | None]:
    participants = event.get("participants", [])
    own = next((p for p in participants if p.get("entity_id") == team_id), None)
    opponent = next((p for p in participants if p.get("entity_id") != team_id), None)
    return own, opponent


def _goals(result: dict, decided_by_shootout: bool) -> int | None:
    """Goals through overtime -- ESPN's final score minus the shootout
    winner's extra goal."""
    periods = result.get("period_scores")
    if periods and decided_by_shootout:
        return sum(periods[:-1])
    return _number(result.get("score"))


def _period_goal(result: dict, period: int) -> int | None:
    return next(iter((result.get("period_scores") or [])[period - 1:period]), None)


def _flag(value) -> int | None:
    return None if value is None else int(value)


def _won(own_result: dict, opponent_result: dict) -> bool | None:
    own_score, opponent_score = _number(own_result.get("score")), _number(opponent_result.get("score"))
    if own_score is None or opponent_score is None:
        return None
    return own_score > opponent_score


def _standings_points(won: bool | None, overtime: bool | None) -> int | None:
    if won is None:
        return None
    if won:
        return 2
    return 1 if overtime else 0


def _result_fields(own_result: dict, opponent_result: dict, event: dict) -> dict:
    shootout = bool(event.get("decided_by_shootout"))
    overtime = event.get("went_to_overtime")
    goals_for, goals_against = _goals(own_result, shootout), _goals(opponent_result, shootout)
    goal_diff = _minus(goals_for, goals_against)
    won = _won(own_result, opponent_result)
    decided = won is not None and overtime is not None
    fields = {
        "goals_for": goals_for,
        "goals_against": goals_against,
        "goal_diff": goal_diff,
        "won": _flag(won),
        "regulation_win": int(won and not overtime) if decided else None,
        "standings_points": _standings_points(won, overtime),
        "went_to_overtime": _flag(overtime),
        "overtime_win": int(won) if decided and overtime else None,
        "one_goal_game": None if goal_diff is None else int(abs(goal_diff) <= 1),
    }
    for period in range(1, REGULATION_PERIODS + 1):
        fields[f"goals_for_p{period}"] = _period_goal(own_result, period)
        fields[f"goals_against_p{period}"] = _period_goal(opponent_result, period)
    return fields


def _box_fields(own_line: dict, opponent_line: dict, goals_for: int | None) -> dict:
    shots_for, shots_against = _stat(own_line, "shots_total"), _stat(opponent_line, "shots_total")
    times_shorthanded = _stat(opponent_line, "power_play_opportunities")
    pp_goals_against = _stat(opponent_line, "power_play_goals")
    return {
        "shots_for": shots_for,
        "shots_against": shots_against,
        "shot_attempts_for": _plus(shots_for, _stat(own_line, "shots_missed"), _stat(opponent_line, "blocked_shots")),
        "shot_attempts_against": _plus(shots_against, _stat(opponent_line, "shots_missed"), _stat(own_line, "blocked_shots")),
        "blocked_shots": _stat(own_line, "blocked_shots"),
        "saves": _stat(own_line, "saves"),
        "goalie_shots_against": _stat(own_line, "shots_against"),
        "pp_goals": _stat(own_line, "power_play_goals"),
        "pp_opportunities": _stat(own_line, "power_play_opportunities"),
        "pp_goals_against": pp_goals_against,
        "times_shorthanded": times_shorthanded,
        "kills": _minus(times_shorthanded, pp_goals_against),
        "penalty_minutes": _stat(own_line, "penalty_minutes"),
        "faceoffs_won": _stat(own_line, "faceoffs_won"),
        "faceoffs_lost": _stat(opponent_line, "faceoffs_won"),
        "takeaways": _stat(own_line, "takeaways"),
        "giveaways": _stat(own_line, "giveaways"),
        "hits": _stat(own_line, "hits"),
        "expected_goals_gap": None if goals_for is None or shots_for is None else goals_for - shots_for * LEAGUE_SHOOTING_PCT,
    }


def team_game_record(event: dict, team_id: str, own_box: dict | None, opponent_box: dict | None) -> dict | None:
    """One team's flat record of one completed game: result, schedule
    context and box-score counts for and against. None if the event has
    no such side. Box-score fields are None without that side's
    team_game_stats row."""
    own, opponent = _side(event, team_id)
    if own is None or opponent is None:
        return None
    is_home = own.get("role") == "home"
    result_fields = _result_fields(own.get("result") or {}, opponent.get("result") or {}, event)
    return {
        "event_key": event.get("event_key"),
        "event_date": event.get("event_date"),
        "kickoff_time": event.get("kickoff_time"),
        "season": event.get("season"),
        "season_type": event.get("season_type"),
        "team_id": team_id,
        "opponent_id": opponent.get("entity_id"),
        "is_home": is_home,
        "site_team_id": team_id if is_home else opponent.get("entity_id"),
        "venue_city": event.get("venue_city"),
        **result_fields,
        **_box_fields(
            (own_box or {}).get("stat_line") or {}, (opponent_box or {}).get("stat_line") or {},
            result_fields["goals_for"],
        ),
    }


# ── Rolling team features ───────────────────────────────────────────────────

def _window_metrics(records: list[dict]) -> dict:
    shooting_pct = _ratio(records, "goals_for", "shots_for", LEAGUE_SHOOTING_PCT, TEAM_SHOT_PRIOR)
    save_pct = _ratio(records, "saves", "goalie_shots_against", LEAGUE_SAVE_PCT, TEAM_SHOT_PRIOR)
    pp_pct = _ratio(records, "pp_goals", "pp_opportunities", LEAGUE_POWER_PLAY_PCT, SPECIAL_TEAMS_PRIOR)
    pk_pct = _ratio(records, "kills", "times_shorthanded", 1 - LEAGUE_POWER_PLAY_PCT, SPECIAL_TEAMS_PRIOR)
    standings_points = _mean(records, "standings_points")
    metrics = {
        "games": len(records),
        "goals_for": _mean(records, "goals_for"),
        "goals_against": _mean(records, "goals_against"),
        "goal_diff": _mean(records, "goal_diff"),
        "shots_for": _mean(records, "shots_for"),
        "shots_against": _mean(records, "shots_against"),
        "shot_share": _share(records, "shots_for", "shots_against"),
        "shot_attempts_for": _mean(records, "shot_attempts_for"),
        "shot_attempts_against": _mean(records, "shot_attempts_against"),
        "shot_attempt_share": _share(records, "shot_attempts_for", "shot_attempts_against"),
        "blocked_shots": _mean(records, "blocked_shots"),
        "shooting_pct": shooting_pct,
        "save_pct": save_pct,
        "pdo": _plus(shooting_pct, save_pct),
        "goals_minus_expected": _mean(records, "expected_goals_gap"),
        "pp_pct": pp_pct,
        "pk_pct": pk_pct,
        "net_special_teams": _minus(_plus(pp_pct, pk_pct), 1.0),
        "pp_opportunities": _mean(records, "pp_opportunities"),
        "times_shorthanded": _mean(records, "times_shorthanded"),
        "penalty_minutes": _mean(records, "penalty_minutes"),
        "faceoff_pct": _share(records, "faceoffs_won", "faceoffs_lost"),
        "takeaways": _mean(records, "takeaways"),
        "giveaways": _mean(records, "giveaways"),
        "hits": _mean(records, "hits"),
        "points_pct": None if standings_points is None else standings_points / 2,
        "win_pct": _mean(records, "won"),
        "regulation_win_pct": _mean(records, "regulation_win"),
        "overtime_rate": _mean(records, "went_to_overtime"),
        "one_goal_game_rate": _mean(records, "one_goal_game"),
    }
    for period in range(1, REGULATION_PERIODS + 1):
        metrics[f"goals_for_p{period}"] = _mean(records, f"goals_for_p{period}")
        metrics[f"goals_against_p{period}"] = _mean(records, f"goals_against_p{period}")
    metrics["third_period_goal_diff"] = _minus(metrics["goals_for_p3"], metrics["goals_against_p3"])
    return metrics


def _streak(records: list[dict]) -> int:
    """Positive = current win streak, negative = current losing streak."""
    streak = 0
    for record in records:
        won = record.get("won")
        if won is None or (streak and (streak > 0) != bool(won)):
            break
        streak += 1 if won else -1
    return streak


def _season_records(records: list[dict], season: int | None) -> list[dict]:
    return [r for r in records if season is not None and r.get("season") == season]


def rolling_team_features(records: list[dict], season: int | None) -> dict:
    """A team's form going into a game of `season`. records: its own
    prior team_game_records, most recent first. Every metric is built over
    the last WINDOWS games and this season to date; a handful also get a
    recency-weighted version, and the arena-scored counts (hits,
    giveaways, takeaways) a road-games-only one."""
    features: dict = {}
    for window in WINDOWS:
        features.update({f"{name}_last{window}": value for name, value in _window_metrics(records[:window]).items()})
    season_to_date = _season_records(records, season)
    features.update({f"{name}_season": value for name, value in _window_metrics(season_to_date).items()})
    # The name library.ml.backtest's early-season holdout split reads.
    features["games_this_season"] = features.pop("games_season")

    long_window = records[:LONG_WINDOW]
    road_games = [r for r in long_window if not r.get("is_home")]
    for field in ("hits", "giveaways", "takeaways"):
        features[f"{field}_road_last{LONG_WINDOW}"] = _mean(road_games, field)

    features["goals_for_ewm"] = _ewm([r["goals_for"] for r in long_window if r.get("goals_for") is not None])
    features["goals_against_ewm"] = _ewm([r["goals_against"] for r in long_window if r.get("goals_against") is not None])
    features["shot_share_ewm"] = _ewm([
        r["shots_for"] / (r["shots_for"] + r["shots_against"]) for r in long_window
        if r.get("shots_for") is not None and r.get("shots_against") is not None and r["shots_for"] + r["shots_against"]
    ])
    features["overtime_win_pct"] = _ratio(
        [{**r, "overtime_game": 1} for r in long_window if r.get("overtime_win") is not None],
        "overtime_win", "overtime_game", 0.5, OVERTIME_GAMES_PRIOR,
    )
    features["win_streak"] = _streak(records)
    features["home_record_pct"] = _mean([r for r in season_to_date if r.get("is_home")], "won")
    features["road_record_pct"] = _mean([r for r in season_to_date if not r.get("is_home")], "won")
    return features


# ── Schedule, rest and travel ───────────────────────────────────────────────

def _days_between(later: str | None, earlier: str | None) -> int | None:
    return rest_days(later, earlier) if later and earlier else None


def _site_coordinates(site_team_id: str | None, venue_city: str | None) -> tuple[float, float] | None:
    return nhl_teams.INTERNATIONAL_VENUES.get(venue_city) or nhl_teams.TEAM_COORDINATES.get(site_team_id)


def _local_start_hour(kickoff_time: str | None, team_id: str) -> int | None:
    """The game's start hour on the clock of team_id's own home market."""
    offset = nhl_teams.TEAM_UTC_OFFSETS.get(team_id)
    if not kickoff_time or offset is None:
        return None
    try:
        return (datetime.fromisoformat(kickoff_time.replace("Z", "+00:00")).hour + offset) % 24
    except ValueError:
        return None


def _consecutive_site_games(records: list[dict], event_date: str, at_home: bool) -> int:
    """How many games in a row, ending with this one, the team has played
    at home (at_home) or on the road."""
    count, later = 1, event_date
    for record in records:
        gap = _days_between(later, record.get("event_date"))
        if gap is None or gap > OFFSEASON_GAP_DAYS or bool(record.get("is_home")) != at_home:
            break
        count += 1
        later = record["event_date"]
    return count


def schedule_features(records: list[dict], event: dict, team_id: str, is_home: bool, home_id: str) -> dict:
    """Rest, schedule density and travel for one side of `event`.
    records: that team's prior team_game_records, most recent first."""
    event_date = event["event_date"]
    previous = records[0] if records else None
    rest = _days_between(event_date, previous.get("event_date")) if previous else None
    in_season = rest is not None and rest <= OFFSEASON_GAP_DAYS

    def games_within(days: int) -> int:
        return sum(1 for r in records if (gap := _days_between(event_date, r.get("event_date"))) is not None and 0 < gap <= days)

    travel_km = timezone_shift = None
    if in_season:
        origin = _site_coordinates(previous.get("site_team_id"), previous.get("venue_city"))
        destination = _site_coordinates(home_id, event.get("venue_city"))
        if origin is not None and destination is not None:
            travel_km = geo.haversine_km(origin, destination)
        timezone_shift = nhl_teams.timezone_shift_hours(previous.get("site_team_id"), home_id)

    return {
        "rest_days": rest,
        "is_back_to_back": None if rest is None else int(rest == 1),
        "games_last_4_days": games_within(4),
        "games_last_7_days": games_within(7),
        "road_trip_game_number": 0 if is_home else _consecutive_site_games(records, event_date, at_home=False),
        "home_stand_game_number": _consecutive_site_games(records, event_date, at_home=True) if is_home else 0,
        "travel_km": travel_km,
        "timezone_shift_hours": timezone_shift,
        "local_start_hour": _local_start_hour(event.get("kickoff_time"), team_id),
    }


# ── Game context ────────────────────────────────────────────────────────────

def _series_state(home_records: list[dict], away_id: str, event: dict) -> dict:
    """Where a playoff series stands going into this game, from the home
    team's own earlier playoff games against away_id this season."""
    if event.get("season_type") != PLAYOFF_SEASON_TYPE:
        return {"series_game_number": 0, "series_lead": 0, "is_elimination_game": 0}
    earlier = [
        r for r in home_records
        if r.get("season") == event.get("season") and r.get("season_type") == PLAYOFF_SEASON_TYPE
        and r.get("opponent_id") == away_id and r.get("won") is not None
    ]
    home_wins = sum(r["won"] for r in earlier)
    away_wins = len(earlier) - home_wins
    return {
        "series_game_number": len(earlier) + 1,
        "series_lead": home_wins - away_wins,
        "is_elimination_game": int(max(home_wins, away_wins) == SERIES_WINS_NEEDED - 1),
    }


def context_features(event: dict, home_id: str, away_id: str, home_records: list[dict]) -> dict:
    season = event.get("season")
    head_to_head = [r for r in home_records if r.get("opponent_id") == away_id][:5]
    is_divisional = nhl_teams.is_divisional_game(home_id, away_id, season)
    is_conference = nhl_teams.is_conference_game(home_id, away_id, season)
    return {
        "is_divisional_game": None if is_divisional is None else int(is_divisional),
        "is_conference_game": None if is_conference is None else int(is_conference),
        "is_playoff": int(event.get("season_type") == PLAYOFF_SEASON_TYPE),
        **_series_state(home_records, away_id, event),
        "is_international_game": int(nhl_teams.is_international_game(event.get("venue_city"))),
        "h2h_goal_diff_last_5": _mean(head_to_head, "goal_diff"),
    }


def team_injury_count(injuries: list[dict] | None) -> int | None:
    """Players listed out, on injured reserve or suspended. None (not 0)
    when the event carries no injury data at all."""
    if injuries is None:
        return None
    return sum(1 for injury in injuries if injury.get("status") in TEAM_INJURY_COUNT_STATUSES)


# ── Lineup availability ─────────────────────────────────────────────────────

def skater_game_line(player_games: list[dict]) -> dict[str, tuple[float, float]]:
    """{skater id: (ice time seconds, points)} for one team's
    player_game_stats rows of one game."""
    line = {}
    for game in player_games:
        if game.get("position_group") != "skater":
            continue
        stats = game.get("stat_line") or {}
        ice_time = _number(stats.get("time_on_ice_seconds"))
        if ice_time:
            line[game["entity_id"]] = (ice_time, _number(stats.get("points")) or 0)
    return line


def lineup_features(recent_lines: list[dict[str, tuple[float, float]]], dressed_ids: set[str] | None) -> dict:
    """How much of a team's regular lineup is missing. recent_lines: its
    last LINEUP_WINDOW_GAMES skater_game_lines, most recent first.
    dressed_ids: the skaters dressed for this game. A regular is a skater
    in at least LINEUP_MIN_GAMES of those games."""
    empty = {"ice_time_share_missing": None, "regulars_missing": None, "top_scorers_out": None}
    lines = recent_lines[:LINEUP_WINDOW_GAMES]
    if dressed_ids is None or len(lines) < LINEUP_MIN_GAMES:
        return empty

    appearances: dict[str, list[tuple[float, float]]] = {}
    for line in lines:
        for player_id, game in line.items():
            appearances.setdefault(player_id, []).append(game)
    regulars = {pid: games for pid, games in appearances.items() if len(games) >= LINEUP_MIN_GAMES}
    if not regulars:
        return empty

    average_ice_time = {pid: sum(t for t, _ in games) / len(games) for pid, games in regulars.items()}
    points_per_60 = {
        pid: sum(p for _, p in games) / sum(t for t, _ in games) * REGULATION_SECONDS for pid, games in regulars.items()
    }
    top_scorers = sorted(points_per_60, key=points_per_60.get, reverse=True)[:TOP_SCORER_COUNT]
    missing = [pid for pid in regulars if pid not in dressed_ids]
    return {
        "ice_time_share_missing": sum(average_ice_time[pid] for pid in missing) / sum(average_ice_time.values()),
        "regulars_missing": len(missing),
        "top_scorers_out": sum(1 for pid in top_scorers if pid not in dressed_ids),
    }


# ── Goalies ─────────────────────────────────────────────────────────────────

def goalie_game_record(player_game: dict, season: int | None) -> dict:
    """One goalie's flat record of one appearance."""
    stats = player_game.get("stat_line") or {}
    saves, shots = _number(stats.get("saves")), _number(stats.get("shots_against"))
    ice_time = _number(stats.get("time_on_ice_seconds"))
    started = bool(player_game.get("started"))
    return {
        "entity_id": player_game.get("entity_id"),
        "team_id": player_game.get("team_id"),
        "event_date": player_game.get("event_date"),
        "season": season,
        "started": started,
        "saves": saves,
        "shots_against": shots,
        "goals_against": _number(stats.get("goals_against")),
        "time_on_ice_seconds": ice_time,
        "saves_above_average": None if saves is None or shots is None else saves - LEAGUE_SAVE_PCT * shots,
        "quality_start": None if not started or not shots or saves is None else int(saves / shots >= LEAGUE_SAVE_PCT),
        "pulled": None if not started or ice_time is None else int(ice_time < PULLED_BEFORE_SECONDS),
    }


def _per_60(records: list[dict], field: str) -> float | None:
    rate = _ratio(records, field, "time_on_ice_seconds")
    return None if rate is None else rate * REGULATION_SECONDS


def _goalie_save_pct(records: list[dict]) -> float | None:
    return _ratio(records, "saves", "shots_against", LEAGUE_SAVE_PCT, GOALIE_SHOT_PRIOR)


_GOALIE_FEATURE_NAMES = (
    *(f"save_pct_last{window}" for window in WINDOWS), "save_pct_season", "save_pct_career",
    "saves_above_average_per_60", "goals_against_per_60", "shots_faced_per_60", "quality_start_rate",
    "pulled_rate", "career_starts", "rest_days", "started_yesterday", "starts_last_7", "starts_last_14",
    "days_since_last_start", "consecutive_starts", "start_share", "is_backup_start", "starter_vs_backup_save_gap",
    *(f"{prefix}_{stat}" for stat in GOALIE_PROP_STATS for prefix in ("avg", "games_with")),
)


def empty_goalie_features() -> dict:
    """Every goalie feature as None -- for a game whose starter is unknown."""
    return dict.fromkeys(_GOALIE_FEATURE_NAMES)


def _tandem_features(goalie_id: str, team_season_goalies: dict[str, list[dict]], team_games_this_season: int) -> dict:
    """team_season_goalies: {goalie id: that goalie's records for this
    team this season}."""
    starts = {gid: sum(1 for r in records if r.get("started")) for gid, records in team_season_goalies.items()}
    own_starts = starts.get(goalie_id, 0)
    share = own_starts / team_games_this_season if team_games_this_season >= MIN_TEAM_GAMES_FOR_START_SHARE else None
    ranked = sorted((gid for gid in starts if starts[gid]), key=starts.get, reverse=True)
    gap = None
    if len(ranked) >= 2:
        gap = _minus(_goalie_save_pct(team_season_goalies[ranked[0]]), _goalie_save_pct(team_season_goalies[ranked[1]]))
    return {
        "start_share": share,
        "is_backup_start": None if share is None else int(share < BACKUP_START_SHARE),
        "starter_vs_backup_save_gap": gap,
    }


def goalie_features(
    goalie_id: str, history: list[dict], team_starter_ids: list[str | None], team_season_goalies: dict[str, list[dict]],
    event_date: str, season: int | None,
) -> dict:
    """The starting goalie's quality, workload and place in his team's
    tandem going into a game.

    history: his own goalie_game_records, most recent first.
    team_starter_ids: who started each of his team's prior games this
    season, most recent first. team_season_goalies: see _tandem_features.
    """
    starts = [r for r in history if r.get("started")]
    long_starts = starts[:LONG_WINDOW]
    last_start_gap = _days_between(event_date, starts[0].get("event_date")) if starts else None

    def starts_within(days: int) -> int:
        return sum(1 for r in starts if (gap := _days_between(event_date, r.get("event_date"))) is not None and 0 < gap <= days)

    consecutive = 0
    for starter_id in team_starter_ids:
        if starter_id != goalie_id:
            break
        consecutive += 1

    features = {f"save_pct_last{window}": _goalie_save_pct(starts[:window]) for window in WINDOWS}
    features.update({
        "save_pct_season": _goalie_save_pct(_season_records(history, season)),
        "save_pct_career": _goalie_save_pct(history),
        "saves_above_average_per_60": _per_60(long_starts, "saves_above_average"),
        "goals_against_per_60": _per_60(long_starts, "goals_against"),
        "shots_faced_per_60": _per_60(long_starts, "shots_against"),
        "quality_start_rate": _mean(long_starts, "quality_start"),
        "pulled_rate": _mean(long_starts, "pulled"),
        "career_starts": len(starts),
        "rest_days": _days_between(event_date, history[0].get("event_date")) if history else None,
        "started_yesterday": None if last_start_gap is None else int(last_start_gap == 1),
        "starts_last_7": starts_within(7),
        "starts_last_14": starts_within(14),
        "days_since_last_start": None if last_start_gap is None else min(last_start_gap, MAX_DAYS_SINCE_LAST_START),
        "consecutive_starts": consecutive,
        **_tandem_features(goalie_id, team_season_goalies, len(team_starter_ids)),
    })
    recent_starts = starts[:PLAYER_WINDOW]
    for stat in GOALIE_PROP_STATS:
        features[f"avg_{stat}"] = _mean(recent_starts, stat)
        features[f"games_with_{stat}"] = sum(1 for r in recent_starts if r.get(stat) is not None)
    return features


# ── Skaters ─────────────────────────────────────────────────────────────────

def _skater_record(player_game: dict) -> dict:
    stats = player_game.get("stat_line") or {}
    shots = _stat(stats, "shots_total")
    return {
        "time_on_ice_seconds": _stat(stats, "time_on_ice_seconds"),
        "pp_time_on_ice_seconds": _stat(stats, "power_play_time_on_ice_seconds"),
        "sh_time_on_ice_seconds": _stat(stats, "short_handed_time_on_ice_seconds"),
        "goals": _stat(stats, "goals"),
        "assists": _stat(stats, "assists"),
        "points": _stat(stats, "points"),
        "shots": shots,
        "shot_attempts": _plus(shots, _stat(stats, "shots_missed")),
        "hits": _stat(stats, "hits"),
        "blocks": _stat(stats, "blocked_shots"),
    }


def skater_features(prior_games: list[dict]) -> dict:
    """A skater's role and production rates. prior_games: his own
    player_game_stats rows, most recent first. Rates are per 60 minutes
    of his own ice time over the last LONG_WINDOW games."""
    records = [_skater_record(game) for game in prior_games[:SKATER_HISTORY_GAMES]]
    long_window = records[:LONG_WINDOW]
    shooting_pct = _ratio(records, "goals", "shots", LEAGUE_SHOOTING_PCT, SKATER_SHOT_PRIOR)
    expected_gaps = [
        r["goals"] - r["shots"] * shooting_pct for r in long_window
        if shooting_pct is not None and r["goals"] is not None and r["shots"] is not None
    ]
    features = {
        f"toi_last{LONG_WINDOW}": _mean(long_window, "time_on_ice_seconds"),
        f"pp_toi_last{LONG_WINDOW}": _mean(long_window, "pp_time_on_ice_seconds"),
        f"sh_toi_last{LONG_WINDOW}": _mean(long_window, "sh_time_on_ice_seconds"),
        "toi_trend": _minus(
            _mean(records[:SKATER_RECENT_GAMES], "time_on_ice_seconds"), _mean(records[:PLAYER_WINDOW], "time_on_ice_seconds"),
        ),
        "shooting_pct_long": shooting_pct,
        f"goals_minus_expected_last{LONG_WINDOW}": sum(expected_gaps) / len(expected_gaps) if expected_gaps else None,
    }
    for stat in ("shots", "shot_attempts", "goals", "assists", "points", "hits", "blocks"):
        features[f"{stat}_per_60"] = _per_60(long_window, stat)
    return features


def position_flags(position: str | None) -> dict:
    """One flag per skater position group; all None when unknown."""
    if not position:
        return {"is_center": None, "is_wing": None, "is_defense": None}
    return {
        "is_center": int(position == "C"),
        "is_wing": int(position in ("LW", "RW", "W", "F")),
        "is_defense": int(position == "D"),
    }


def role_features(player_id: str, recent_lines: list[dict[str, tuple[float, float]]]) -> dict:
    """Where a skater sits in his team's recent lineup. recent_lines: the
    team's last LINEUP_WINDOW_GAMES skater_game_lines, most recent first."""
    lines = recent_lines[:LINEUP_WINDOW_GAMES]
    ice_times: dict[str, list[float]] = {}
    for line in lines:
        for skater_id, (ice_time, _) in line.items():
            ice_times.setdefault(skater_id, []).append(ice_time)
    if player_id not in ice_times:
        return {"games_missed_last_10": len(lines) if lines else None, "toi_rank_on_team": None}
    averages = {skater_id: sum(times) / len(times) for skater_id, times in ice_times.items()}
    return {
        "games_missed_last_10": len(lines) - len(ice_times[player_id]),
        "toi_rank_on_team": 1 + sum(1 for average in averages.values() if average > averages[player_id]),
    }


# ── Row builders ────────────────────────────────────────────────────────────

def _home_away(event: dict) -> tuple[dict, dict]:
    participants = event["participants"]
    return (
        next(p for p in participants if p.get("role") == "home"),
        next(p for p in participants if p.get("role") == "away"),
    )


def _expected_pp_goals(own: dict, opponent: dict) -> float | None:
    """Own power plays drawn blended with the opponent's times
    shorthanded, times own conversion blended with what the opponent's
    penalty kill allows."""
    suffix = f"_last{LONG_WINDOW}"
    opportunities = _plus(own.get(f"pp_opportunities{suffix}"), opponent.get(f"times_shorthanded{suffix}"))
    opponent_pk = opponent.get(f"pk_pct{suffix}")
    conversion = _plus(own.get(f"pp_pct{suffix}"), None if opponent_pk is None else 1 - opponent_pk)
    return None if opportunities is None or conversion is None else (opportunities / 2) * (conversion / 2)


def build_event_features(
    event: dict,
    elo_ratings: dict[str, dict[str, float]],
    home_records: list[dict],
    away_records: list[dict],
    *,
    home_goalie: dict | None = None,
    away_goalie: dict | None = None,
    home_lineup: dict | None = None,
    away_lineup: dict | None = None,
) -> dict:
    """One training row for a game: win/score labels plus both sides'
    features.

    home_records/away_records: each team's prior team_game_records, most
    recent first, NOT including this game. home_goalie/away_goalie: the
    starter's goalie_features (None when unknown). home_lineup/
    away_lineup: lineup_features.

    label_home_score/label_away_score are ESPN's final score, which
    includes a shootout winner's extra goal; label_home_goals/
    label_away_goals are goals through overtime.
    """
    home, away = _home_away(event)
    home_id, away_id = home["entity_id"], away["entity_id"]
    season = event.get("season")
    ratings = elo_ratings.get(event["event_key"], {})
    home_elo, away_elo = ratings.get("home_pre_rating"), ratings.get("away_pre_rating")

    sides = {
        "home": {
            **rolling_team_features(home_records, season),
            **schedule_features(home_records, event, home_id, True, home_id),
            "team_injury_count": team_injury_count(event.get("home_injuries")),
            **(home_lineup or lineup_features([], None)),
            **{f"goalie_{name}": value for name, value in (home_goalie or empty_goalie_features()).items()},
        },
        "away": {
            **rolling_team_features(away_records, season),
            **schedule_features(away_records, event, away_id, False, home_id),
            "team_injury_count": team_injury_count(event.get("away_injuries")),
            **(away_lineup or lineup_features([], None)),
            **{f"goalie_{name}": value for name, value in (away_goalie or empty_goalie_features()).items()},
        },
    }
    sides["home"]["expected_pp_goals"] = _expected_pp_goals(sides["home"], sides["away"])
    sides["away"]["expected_pp_goals"] = _expected_pp_goals(sides["away"], sides["home"])

    home_result, away_result = home.get("result") or {}, away.get("result") or {}
    shootout = bool(event.get("decided_by_shootout"))
    row = {
        "event_key": event["event_key"],
        "event_date": event["event_date"],
        "season": season,
        "home_entity_id": home_id,
        "away_entity_id": away_id,
        "home_elo": home_elo,
        "away_elo": away_elo,
        "elo_diff": _minus(home_elo, away_elo),
        **context_features(event, home_id, away_id, home_records),
    }
    for side, features in sides.items():
        row.update({f"{side}_{name}": value for name, value in features.items()})
    for metric in DIFF_METRICS:
        row[f"diff_{metric}"] = _minus(sides["home"].get(metric), sides["away"].get(metric))
    row.update({
        "label_home_won": home_result.get("won"),
        "label_home_score": home_result.get("score"),
        "label_away_score": away_result.get("score"),
        "label_home_goals": _goals(home_result, shootout),
        "label_away_goals": _goals(away_result, shootout),
        "label_went_to_overtime": event.get("went_to_overtime"),
        "label_decided_by_shootout": event.get("decided_by_shootout"),
    })
    return row


_SKATER_TEAM_METRICS = ("goals_for", "shots_for", "shot_attempts_for", "pp_opportunities", "pp_pct")
_SKATER_OPPONENT_METRICS = (
    "goals_against", "shots_against", "shot_attempts_against", "blocked_shots", "hits", "times_shorthanded",
    "pk_pct", "save_pct",
)


def build_player_features(
    player_game: dict,
    prior_games: list[dict],
    event: dict,
    elo_ratings: dict[str, dict[str, float]],
    own_records: list[dict],
    opponent_records: list[dict],
    *,
    opponent_goalie: dict | None = None,
    recent_lines: list[dict] | None = None,
) -> dict:
    """One training row for a skater's props. prior_games: his own
    player_game_stats rows, most recent first, NOT including this game.

    avg_<stat>/games_with_<stat> cover his last PLAYER_WINDOW games --
    the columns the shared prop trainer filters and baselines on.
    opponent_goalie: the opposing starter's goalie_features."""
    home, away = _home_away(event)
    team_id = player_game["team_id"]
    is_home = team_id == home["entity_id"]
    ratings = elo_ratings.get(event["event_key"], {})
    home_elo, away_elo = ratings.get("home_pre_rating"), ratings.get("away_pre_rating")
    own_elo, opponent_elo = (home_elo, away_elo) if is_home else (away_elo, home_elo)
    season = event.get("season")
    own_form = rolling_team_features(own_records, season)
    opponent_form = rolling_team_features(opponent_records, season)
    own_schedule = schedule_features(own_records, event, team_id, is_home, home["entity_id"])
    goalie = opponent_goalie or empty_goalie_features()
    suffix = f"_last{LONG_WINDOW}"

    return {
        "event_key": player_game["event_key"],
        "player_key": player_game["player_key"],
        "entity_id": player_game["entity_id"],
        "team_id": team_id,
        "opponent_id": away["entity_id"] if is_home else home["entity_id"],
        "event_date": player_game["event_date"],
        **rolling_player_stat_averages(prior_games[:PLAYER_WINDOW], PLAYER_WINDOW, as_of=player_game["event_date"]),
        **skater_features(prior_games),
        **position_flags(player_game.get("position")),
        **role_features(player_game["entity_id"], recent_lines or []),
        "is_home": int(is_home),
        "is_playoff": int(event.get("season_type") == PLAYOFF_SEASON_TYPE),
        "rest_days": own_schedule["rest_days"],
        "is_back_to_back": own_schedule["is_back_to_back"],
        "own_elo": own_elo,
        "opponent_elo": opponent_elo,
        "elo_diff": _minus(own_elo, opponent_elo),
        **{f"team_{metric}{suffix}": own_form.get(f"{metric}{suffix}") for metric in _SKATER_TEAM_METRICS},
        **{f"opponent_{metric}{suffix}": opponent_form.get(f"{metric}{suffix}") for metric in _SKATER_OPPONENT_METRICS},
        "opponent_goalie_save_pct_season": goalie["save_pct_season"],
        f"opponent_goalie_save_pct{suffix}": goalie[f"save_pct{suffix}"],
        "label_stat_line": player_game.get("stat_line", {}),
        "label_started": player_game.get("started"),
    }


_OPPONENT_OFFENSE_METRICS = ("shots_for", "shot_attempts_for", "goals_for", "shooting_pct", "pp_pct", "pp_opportunities")
_OWN_DEFENSE_METRICS = ("shots_against", "shot_attempts_against", "goals_against", "blocked_shots", "times_shorthanded", "pk_pct")


def build_goalie_features(
    player_game: dict,
    event: dict,
    elo_ratings: dict[str, dict[str, float]],
    goalie: dict,
    own_records: list[dict],
    opponent_records: list[dict],
) -> dict:
    """One training row for a starting goalie's saves/goals-against
    props: his goalie_features, the opponent's offence, his own team's
    defence, and label_stat_line (his real stat line)."""
    home, away = _home_away(event)
    team_id = player_game["team_id"]
    is_home = team_id == home["entity_id"]
    opponent_id = away["entity_id"] if is_home else home["entity_id"]
    ratings = elo_ratings.get(event["event_key"], {})
    home_elo, away_elo = ratings.get("home_pre_rating"), ratings.get("away_pre_rating")
    own_elo, opponent_elo = (home_elo, away_elo) if is_home else (away_elo, home_elo)
    season = event.get("season")
    own_form = rolling_team_features(own_records, season)
    opponent_form = rolling_team_features(opponent_records, season)
    own_schedule = schedule_features(own_records, event, team_id, is_home, home["entity_id"])

    row = {
        "event_key": player_game["event_key"],
        "player_key": player_game["player_key"],
        "entity_id": player_game["entity_id"],
        "team_id": team_id,
        "opponent_id": opponent_id,
        "event_date": player_game["event_date"],
        "is_home": int(is_home),
        "is_playoff": int(event.get("season_type") == PLAYOFF_SEASON_TYPE),
        "own_elo": own_elo,
        "opponent_elo": opponent_elo,
        "elo_diff": _minus(own_elo, opponent_elo),
        "team_rest_days": own_schedule["rest_days"],
        "team_is_back_to_back": own_schedule["is_back_to_back"],
        **goalie,
    }
    for window in WINDOWS:
        row.update({f"opponent_{m}_last{window}": opponent_form.get(f"{m}_last{window}") for m in _OPPONENT_OFFENSE_METRICS})
        row.update({f"team_{m}_last{window}": own_form.get(f"{m}_last{window}") for m in _OWN_DEFENSE_METRICS})
    row["label_stat_line"] = player_game.get("stat_line", {})
    row["label_started"] = player_game.get("started")
    return row


# ── Feature groups ──────────────────────────────────────────────────────────

# Event-row feature groups, by metric name (side prefix and window suffix
# stripped). Training keeps or drops a whole group at a time.
FEATURE_GROUPS: dict[str, frozenset[str]] = {
    "elo": frozenset({"elo", "elo_diff"}),
    "scoring_form": frozenset({
        "goals_for", "goals_against", "goal_diff", "points_pct", "win_pct", "regulation_win_pct", "win_streak",
        "games", "games_this_season", "home_record_pct", "road_record_pct",
    }),
    "shot_volume": frozenset({
        "shots_for", "shots_against", "shot_share", "shot_attempts_for", "shot_attempts_against",
        "shot_attempt_share", "blocked_shots",
    }),
    "finishing": frozenset({"shooting_pct", "save_pct", "pdo", "goals_minus_expected"}),
    "special_teams": frozenset({
        "pp_pct", "pk_pct", "net_special_teams", "pp_opportunities", "times_shorthanded", "penalty_minutes",
        "expected_pp_goals",
    }),
    "possession": frozenset({"faceoff_pct", "takeaways", "giveaways", "hits"}),
    "game_state": frozenset({
        *(f"goals_{direction}_p{period}" for direction in ("for", "against") for period in range(1, REGULATION_PERIODS + 1)),
        "third_period_goal_diff", "overtime_rate", "one_goal_game_rate", "overtime_win_pct",
    }),
    "schedule": frozenset({
        "rest_days", "is_back_to_back", "games_last_4_days", "games_last_7_days", "road_trip_game_number",
        "home_stand_game_number", "travel_km", "timezone_shift_hours", "local_start_hour",
    }),
    "context": frozenset({
        "is_divisional_game", "is_conference_game", "is_playoff", "series_game_number", "series_lead",
        "is_elimination_game", "is_international_game", "h2h_goal_diff_last_5",
    }),
    "lineup": frozenset({"ice_time_share_missing", "regulars_missing", "top_scorers_out", "team_injury_count"}),
}
GOALIE_FEATURE_GROUP = "goalie"

_SIDE_PREFIX = re.compile(r"^(home|away|diff)_")
_WINDOW_SUFFIX = re.compile(r"_(road_last\d+|last\d+|season|ewm)$")
_NON_FEATURE_EVENT_COLUMNS = frozenset({"event_key", "event_date", "season", "home_entity_id", "away_entity_id"})


def feature_group(column: str) -> str | None:
    """The FEATURE_GROUPS key (or "goalie") an event-row column belongs
    to; None for an identifier or label column."""
    if column in _NON_FEATURE_EVENT_COLUMNS or column.startswith("label_"):
        return None
    name = _SIDE_PREFIX.sub("", column)
    if name.startswith("goalie_"):
        return GOALIE_FEATURE_GROUP
    for candidate in (name, _WINDOW_SUFFIX.sub("", name)):
        for group, metrics in FEATURE_GROUPS.items():
            if candidate in metrics:
                return group
    return None


def event_feature_columns(groups: frozenset[str] | set[str]) -> frozenset[str]:
    """Every event-row feature column belonging to any of `groups`."""
    blank_event = {
        "event_key": "", "event_date": "2000-01-01",
        "participants": [{"entity_id": "home", "role": "home"}, {"entity_id": "away", "role": "away"}],
    }
    return frozenset(column for column in build_event_features(blank_event, {}, [], []) if feature_group(column) in groups)
