"""
Pure NCAAFB feature-computation functions -- the CFBD-sourced equivalent
of library.features.nfl. No AWS calls here; every function takes
already-fetched rows and returns numbers.

- is_conference_game reads CFBD's own per-game conference_game flag
  (already season-scoped) instead of a hardcoded division lookup.
- build_event_features/build_player_features take a team_coordinates
  dict built from team entity data at call time (CFBD's /teams response
  carries each team's home-stadium lat/long directly) instead of a
  hardcoded module constant. No international-venue table exists yet;
  travel distance is computed as if the "home" team were still at its
  own market.
- is_bowl_game/is_playoff_game identify CFP games from CFBD's own
  `playoff` field, not a bowl-name string match.
- No injury or depth-chart data exists for college football, so
  build_event_features never computes injury-derived fields.
- No career_playoff_win_pct -- CFBD folds bowl/playoff results into the
  same season win-loss total a coach's record already reports.
- build_team_week_features is a team-week granularity row (not
  event-level or player-level) for the National Ranking model, labeled
  with CFBD's own AP Top 25 rank when enrichment attached one to that
  team's game that week.
"""
from library.features import geo
from library.features.common import (
    DEFAULT_ROLLING_WINDOW,
    _identify_leader,
    _rate,
    _season_record,
    average_opponent_elo,
    current_streak,
    kickoff_hour_utc,
    rest_days,
    rolling_player_stat_averages,
    rolling_team_scoring_averages,
)


def identify_starting_qb(team_player_games: list[dict]) -> dict | None:
    """The player with the most passing attempts -- see _identify_leader.
    Requires normalize.ncaafb's "C/ATT" compound split (passing_attempts),
    not the raw combined CFBD field."""
    return _identify_leader(team_player_games, "passing_attempts")


def identify_lead_rusher(team_player_games: list[dict]) -> dict | None:
    """The player with the most rushing attempts (CFBD's CAR, mapped to
    "attempts" by normalize.ncaafb) -- see _identify_leader."""
    return _identify_leader(team_player_games, "rushing_attempts")


def identify_lead_receiver(team_player_games: list[dict]) -> dict | None:
    """The player with the most receptions -- CFBD's box score has no
    targets stat (unlike ESPN's), so receptions is the volume signal
    here instead, a documented divergence from NFL's identify_lead_receiver."""
    return _identify_leader(team_player_games, "receiving_receptions")


def is_conference_game(event: dict) -> bool | None:
    """CFBD's own conference_game flag, passed through as-is by
    normalize.ncaafb.game_to_event_item -- already season-scoped (FBS
    conference membership changes ~yearly), so no static lookup table is
    needed the way NFL's is_divisional_game uses one. None if CFBD hasn't
    computed it for this game yet (e.g. a far-future scheduled game)."""
    return event.get("conference_game")


def is_playoff_game(event: dict) -> bool:
    """True only for a real 12-team CFP game -- see game_to_event_item's
    own docstring for how this is derived from CFBD's `playoff` field."""
    return bool(event.get("is_playoff_game"))


def is_bowl_game(event: dict) -> bool:
    """Any postseason game that ISN'T a CFP game -- the ~40 unaffiliated
    bowls, distinct from is_playoff_game."""
    return event.get("season_type") == "postseason" and not is_playoff_game(event)


def build_event_features(
    event: dict,
    elo_ratings: dict[str, dict[str, float]],
    home_team_events: list[dict],
    away_team_events: list[dict],
    team_coordinates: dict[str, tuple[float, float]],
    window: int = DEFAULT_ROLLING_WINDOW,
    home_qb_games: list[dict] | None = None,
    away_qb_games: list[dict] | None = None,
    home_rb_games: list[dict] | None = None,
    away_rb_games: list[dict] | None = None,
    home_wr_games: list[dict] | None = None,
    away_wr_games: list[dict] | None = None,
    home_team_box_stats: list[dict] | None = None,
    away_team_box_stats: list[dict] | None = None,
) -> dict:
    """Assembles one training row for a head-to-head event -- same shape
    and calling convention as library.features.nfl.build_event_features
    (see its own docstring for what each argument means); team_coordinates
    is the one addition, {entity_id: (latitude, longitude)} built from
    team entities' own metadata (see library/normalize/ncaafb.py's
    team_to_entity) rather than a hardcoded module constant.

    No raw season_type column -- unlike build_team_week_features (the
    ranking model's own row, which deliberately excludes it from training
    via NON_FEATURE_COLUMNS instead), a raw "regular"/"postseason" string
    passed straight into training_common.numeric_frame's pd.to_numeric
    coercion becomes NaN on every row, silently contributing nothing.
    is_bowl_game/is_playoff_game below already give the model the same
    postseason signal as real, usable numeric features (regular season is
    simply both False), so there's nothing left for a raw season_type
    column to add.
    """
    participants = event["participants"]
    home = next(p for p in participants if p.get("role") == "home")
    away = next(p for p in participants if p.get("role") == "away")
    home_id, away_id = home["entity_id"], away["entity_id"]

    ratings = elo_ratings.get(event["event_key"], {})
    home_elo = ratings.get("home_pre_rating")
    away_elo = ratings.get("away_pre_rating")

    home_scoring = rolling_team_scoring_averages(home_team_events, home_id, window)
    away_scoring = rolling_team_scoring_averages(away_team_events, away_id, window)

    home_qb_stats = rolling_player_stat_averages(home_qb_games or [], window)
    away_qb_stats = rolling_player_stat_averages(away_qb_games or [], window)
    home_rb_stats = rolling_player_stat_averages(home_rb_games or [], window)
    away_rb_stats = rolling_player_stat_averages(away_rb_games or [], window)
    home_wr_stats = rolling_player_stat_averages(home_wr_games or [], window)
    away_wr_stats = rolling_player_stat_averages(away_wr_games or [], window)

    home_box_stats = rolling_player_stat_averages(home_team_box_stats or [], window)
    away_box_stats = rolling_player_stat_averages(away_team_box_stats or [], window)
    home_third_down_pct = _rate(home_box_stats, "avg_third_down_conversions", "avg_third_down_attempts")
    away_third_down_pct = _rate(away_box_stats, "avg_third_down_conversions", "avg_third_down_attempts")

    home_win_streak = current_streak(home_team_events, home_id)
    away_win_streak = current_streak(away_team_events, away_id)

    # venue_city always None here -- see this module's own docstring on
    # why there's no international-venue table yet; every game computes
    # travel as if played at the home team's own market.
    home_travel_km, away_travel_km = geo.travel_distances_km(away_id, home_id, None, team_coordinates, {})

    return {
        "event_key": event["event_key"],
        "event_date": event["event_date"],
        "home_entity_id": home_id,
        "away_entity_id": away_id,
        "week": event.get("week"),
        "kickoff_hour_utc": kickoff_hour_utc(event.get("kickoff_time")),
        "venue_indoor": event.get("venue_indoor"),
        "home_elo": home_elo,
        "away_elo": away_elo,
        "elo_diff": (home_elo - away_elo) if home_elo is not None and away_elo is not None else None,
        "home_rest_days": rest_days(event["event_date"], home_team_events[0]["event_date"]) if home_team_events else None,
        "away_rest_days": rest_days(event["event_date"], away_team_events[0]["event_date"]) if away_team_events else None,
        "home_avg_points_scored": home_scoring["avg_points_scored"],
        "home_avg_points_allowed": home_scoring["avg_points_allowed"],
        "home_games_played": home_scoring["games_played"],
        "away_avg_points_scored": away_scoring["avg_points_scored"],
        "away_avg_points_allowed": away_scoring["avg_points_allowed"],
        "away_games_played": away_scoring["games_played"],
        "home_qb_avg_passing_yards": home_qb_stats.get("avg_passing_yards"),
        "home_qb_avg_passing_tds": home_qb_stats.get("avg_passing_touchdowns"),
        "home_qb_avg_interceptions": home_qb_stats.get("avg_passing_interceptions"),
        "home_qb_games_played": home_qb_stats["games_played"],
        "away_qb_avg_passing_yards": away_qb_stats.get("avg_passing_yards"),
        "away_qb_avg_passing_tds": away_qb_stats.get("avg_passing_touchdowns"),
        "away_qb_avg_interceptions": away_qb_stats.get("avg_passing_interceptions"),
        "away_qb_games_played": away_qb_stats["games_played"],
        "home_rb_avg_rushing_yards": home_rb_stats.get("avg_rushing_yards"),
        "home_rb_avg_rushing_tds": home_rb_stats.get("avg_rushing_touchdowns"),
        "home_rb_games_played": home_rb_stats["games_played"],
        "away_rb_avg_rushing_yards": away_rb_stats.get("avg_rushing_yards"),
        "away_rb_avg_rushing_tds": away_rb_stats.get("avg_rushing_touchdowns"),
        "away_rb_games_played": away_rb_stats["games_played"],
        "home_wr_avg_receiving_yards": home_wr_stats.get("avg_receiving_yards"),
        "home_wr_avg_receiving_tds": home_wr_stats.get("avg_receiving_touchdowns"),
        "home_wr_avg_receptions": home_wr_stats.get("avg_receiving_receptions"),
        "home_wr_games_played": home_wr_stats["games_played"],
        "away_wr_avg_receiving_yards": away_wr_stats.get("avg_receiving_yards"),
        "away_wr_avg_receiving_tds": away_wr_stats.get("avg_receiving_touchdowns"),
        "away_wr_avg_receptions": away_wr_stats.get("avg_receiving_receptions"),
        "away_wr_games_played": away_wr_stats["games_played"],
        "home_avg_turnovers": home_box_stats.get("avg_turnovers"),
        "home_avg_total_yards": home_box_stats.get("avg_total_yards"),
        "home_avg_possession_time_seconds": home_box_stats.get("avg_possession_time_seconds"),
        "home_avg_penalties": home_box_stats.get("avg_penalties"),
        "home_avg_penalty_yards": home_box_stats.get("avg_penalty_yards"),
        "home_third_down_pct": home_third_down_pct,
        "home_box_games_played": home_box_stats["games_played"],
        "away_avg_turnovers": away_box_stats.get("avg_turnovers"),
        "away_avg_total_yards": away_box_stats.get("avg_total_yards"),
        "away_avg_possession_time_seconds": away_box_stats.get("avg_possession_time_seconds"),
        "away_avg_penalties": away_box_stats.get("avg_penalties"),
        "away_avg_penalty_yards": away_box_stats.get("avg_penalty_yards"),
        "away_third_down_pct": away_third_down_pct,
        "away_box_games_played": away_box_stats["games_played"],
        "is_conference_game": is_conference_game(event),
        "is_bowl_game": is_bowl_game(event),
        "is_playoff_game": is_playoff_game(event),
        "home_travel_km": home_travel_km,
        "away_travel_km": away_travel_km,
        "home_win_streak": home_win_streak,
        "away_win_streak": away_win_streak,
        "home_coach_experience": event.get("home_coach_experience"),
        "away_coach_experience": event.get("away_coach_experience"),
        "home_coach_season_win_pct": event.get("home_coach_season_win_pct"),
        "away_coach_season_win_pct": event.get("away_coach_season_win_pct"),
        "home_current_rank": event.get("home_current_rank"),
        "away_current_rank": event.get("away_current_rank"),
        "label_home_won": home.get("result", {}).get("won"),
        "label_home_score": home.get("result", {}).get("score"),
        "label_away_score": away.get("result", {}).get("score"),
    }


def build_player_features(
    player_game: dict,
    prior_games: list[dict],
    event: dict,
    elo_ratings: dict[str, dict[str, float]],
    own_previous_event_date: str | None,
    team_coordinates: dict[str, tuple[float, float]],
    window: int = DEFAULT_ROLLING_WINDOW,
) -> dict:
    """One training row for a player-prop target -- same shape as
    library.features.nfl.build_player_features (see its own docstring),
    plus team_coordinates for the same reason build_event_features takes
    it. No raw season_type column, same reasoning as build_event_features'
    own docstring -- is_bowl_game/is_playoff_game below already carry the
    signal as usable numeric features."""
    participants = event["participants"]
    home = next(p for p in participants if p.get("role") == "home")
    away = next(p for p in participants if p.get("role") == "away")
    home_id, away_id = home["entity_id"], away["entity_id"]
    team_id = player_game["team_id"]
    is_home = team_id == home_id
    opponent_id = away_id if is_home else home_id

    ratings = elo_ratings.get(event["event_key"], {})
    home_elo = ratings.get("home_pre_rating")
    away_elo = ratings.get("away_pre_rating")
    own_elo = home_elo if is_home else away_elo
    opponent_elo = away_elo if is_home else home_elo

    home_travel_km, away_travel_km = geo.travel_distances_km(away_id, home_id, None, team_coordinates, {})
    own_travel_km = home_travel_km if is_home else away_travel_km

    averages = rolling_player_stat_averages(prior_games, window)
    return {
        "event_key": player_game["event_key"],
        "player_key": player_game["player_key"],
        "entity_id": player_game["entity_id"],
        "team_id": team_id,
        "opponent_id": opponent_id,
        "event_date": player_game["event_date"],
        **averages,
        "is_home": is_home,
        "week": event.get("week"),
        "kickoff_hour_utc": kickoff_hour_utc(event.get("kickoff_time")),
        "venue_indoor": event.get("venue_indoor"),
        "rest_days": rest_days(player_game["event_date"], own_previous_event_date),
        "own_elo": own_elo,
        "opponent_elo": opponent_elo,
        "elo_diff": (own_elo - opponent_elo) if own_elo is not None and opponent_elo is not None else None,
        "is_conference_game": is_conference_game(event),
        "is_bowl_game": is_bowl_game(event),
        "is_playoff_game": is_playoff_game(event),
        "travel_km": own_travel_km,
        "label_stat_line": player_game.get("stat_line", {}),
        "label_started": player_game.get("started"),
    }


def build_team_week_features(
    team_id: str,
    event: dict,
    elo_ratings: dict[str, dict[str, float]],
    team_season_events: list[dict],
) -> dict:
    """One team-week training row for the National Ranking model --
    team_id's own state heading into `event` (the game that pins this
    row to a specific week), labeled with CFBD's AP Top 25 rank for that
    week if ingest/backfill's enrichment attached one to this side of the
    game (see library/normalize/ncaafb.py's game_to_event_item) -- None
    (excluded from training by train_ranking_model.py, same "missing, not
    fabricated" discipline as every other sparse-optional field in this
    project) for an unranked team-week.

    Unlike build_event_features, this isn't a home/away pair -- called
    once per side per event by feature-engineering's build_dataset.py.
    team_season_events must already be scoped to team_id's own games
    within the SAME SEASON as `event`, most recent first, NOT including
    `event` itself -- record/scoring/streak/strength-of-schedule are
    season-to-date figures here, not a trailing N-game rolling window the
    way the other three models' window parameter works, since a national
    ranking reflects a team's whole season, not just its last 5 games.
    """
    participants = event["participants"]
    home = next(p for p in participants if p.get("role") == "home")
    away = next(p for p in participants if p.get("role") == "away")
    is_home = team_id == home["entity_id"]
    conference = event.get("home_conference") if is_home else event.get("away_conference")
    current_rank = event.get("home_current_rank") if is_home else event.get("away_current_rank")

    ratings = elo_ratings.get(event["event_key"], {})
    own_elo = ratings.get("home_pre_rating") if is_home else ratings.get("away_pre_rating")

    scoring = rolling_team_scoring_averages(team_season_events, team_id, window=len(team_season_events))
    wins, losses = _season_record(team_season_events, team_id)

    return {
        "event_key": event["event_key"],
        "team_id": team_id,
        "event_date": event["event_date"],
        "season": event.get("season"),
        "week": event.get("week"),
        "season_type": event.get("season_type"),
        "conference": conference,
        "elo": own_elo,
        "wins": wins,
        "losses": losses,
        "games_played": len(team_season_events),
        "avg_points_scored": scoring["avg_points_scored"],
        "avg_points_allowed": scoring["avg_points_allowed"],
        "win_streak": current_streak(team_season_events, team_id),
        "strength_of_schedule": average_opponent_elo(team_season_events, team_id, elo_ratings),
        "label_current_rank": current_rank,
    }
