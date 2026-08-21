"""
Pure NCAA MBB feature-computation functions -- the ESPN-sourced,
basketball equivalent of library.features.nba. Same split as every other
sport's feature module: no AWS calls here, every function takes
already-fetched rows and returns numbers, and train-time
(Source/feature-engineering/ncaambb/build_dataset.py) and live-serve-time
feature computation share these same functions once inference lands
(step 6).

Genuinely different from NBA, not just renamed:
- avg_rebounds is read DIRECTLY off box_stats, not derived from
  offensive+defensive the way NBA's own _total_rebounds works around a
  real data gap -- confirmed live, 2026-08-19 (see
  project-ncaambb-onboarding memory): ESPN's NCAA MBB box score DOES
  carry a raw combined "rebounds" stat alongside offensiveRebounds/
  defensiveRebounds, unlike NBA's, where "avg_rebounds" came back null.
  Trusting the raw stat here rather than porting NBA's derivation
  workaround into a sport that doesn't need it.
- is_conference_game replaces NBA's is_divisional_game -- ESPN's own
  conferenceCompetition flag (library/normalize/espn.py's
  scoreboard_event_to_event_item, confirmed live 2026-08-20), true for
  both a regular-season conference game and a conference-tournament game.
  No static division/conference table needed, unlike NBA's
  library.features.nba_teams (real yearly conference realignment makes a
  hand-maintained table the wrong tool here -- same reasoning
  project-phase3-nba-ncaambb-plan already flagged for conference
  membership generally).
- No travel_km/is_international_game -- confirmed live, 2026-08-20: ESPN
  has NO geo-coordinates anywhere for NCAA MBB teams/venues (checked both
  the site API's team resource and the core API's team/venue resources --
  only city/state text, never lat/long), unlike NCAAFB's CFBD source
  (which embeds each team's home-stadium coordinates directly) or NBA's
  own static 30-team table (a small enough, low-realignment set to hand-
  maintain safely). Hand-typing coordinates for ~362 teams would repeat
  the exact "2 of NBA's 30 hand-typed ids were wrong until checked
  individually" risk the plan already flagged, at 12x the scale -- dropped
  rather than guessed, same "don't feature-engineer something you can't
  reliably compute" discipline as every other omission in this docstring.
- No National Ranking model features here -- build_team_week_features
  (the AP-poll-labeled national-ranking model's own feature builder) is a
  separate, later addition (see project-ncaambb-onboarding memory);
  build_event_features/build_player_features below are the 4 core
  training targets (win-probability, score margin/home/away, 6
  player-props) shared by every sport's step-5 build.
- No coach-tenure features, no venue_indoor -- same reasoning as NBA's
  own docstring (no coach data source; every arena is indoor, so the
  field would be a constant with no discriminating value).
- home_team_injury_count/away_team_injury_count only, no per-player
  injury-status equivalent -- same "no single dominant position the way
  a starting QB is" reasoning as NBA's own docstring. Sourced from
  aws-lambdas/ncaambb/ingest/handler.py's _attach_injuries, confirmed
  live that NCAA MBB's roster response embeds injuries the same shape as
  NBA's.
- No season_type/week columns in build_player_features -- NCAA MBB's
  schedule is date-based, not week-based, same as NBA's.
"""
from library.features.common import (
    DEFAULT_ROLLING_WINDOW,
    _efficiency_per_100,
    _rate,
    _season_record,
    _team_injury_count,
    average_opponent_elo,
    current_streak,
    estimate_possessions,
    kickoff_hour_utc,
    rest_days,
    rolling_player_stat_averages,
    rolling_team_scoring_averages,
)


def is_conference_game(event: dict) -> bool | None:
    """ESPN's own conferenceCompetition flag, passed through as-is by
    library.normalize.espn.scoreboard_event_to_event_item -- true for
    both a regular-season conference game and a conference-tournament
    game (see that function's own docstring), false for a non-conference
    game or an NCAA-tournament game. None if ESPN hasn't set the field at
    all (an older/backfilled event predating this passthrough)."""
    return event.get("conference_competition")


def build_event_features(
    event: dict,
    elo_ratings: dict[str, dict[str, float]],
    home_team_events: list[dict],
    away_team_events: list[dict],
    window: int = DEFAULT_ROLLING_WINDOW,
    home_team_box_stats: list[dict] | None = None,
    away_team_box_stats: list[dict] | None = None,
) -> dict:
    """Assembles one training row for a head-to-head event: game-outcome
    (win/loss) and game-score features and labels share this same row,
    same convention as library.features.nba.build_event_features.

    elo_ratings is compute_elo_ratings' full output, called by this
    module's own caller (build_dataset.py) with library.features.common's
    NFL-tuned default constants -- basketball's own tuned constants are a
    known open item, deliberately not guessed here, same as NBA's own
    build_event_features. home_team_events/away_team_events are each
    team's own prior completed events, most recent first, NOT including
    this one.

    home_team_box_stats/away_team_box_stats are each team's own prior
    team_game_stats rows, most recent first, NOT including this one. No
    home_qb_games-style per-position argument -- see this module's own
    docstring for why.
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

    home_box_stats = rolling_player_stat_averages(home_team_box_stats or [], window)
    away_box_stats = rolling_player_stat_averages(away_team_box_stats or [], window)

    home_possessions = estimate_possessions(
        home_box_stats.get("avg_field_goal_attempts"), home_box_stats.get("avg_offensive_rebounds"),
        home_box_stats.get("avg_turnovers"), home_box_stats.get("avg_free_throw_attempts"),
    )
    away_possessions = estimate_possessions(
        away_box_stats.get("avg_field_goal_attempts"), away_box_stats.get("avg_offensive_rebounds"),
        away_box_stats.get("avg_turnovers"), away_box_stats.get("avg_free_throw_attempts"),
    )
    home_offensive_efficiency = _efficiency_per_100(home_scoring["avg_points_scored"], home_possessions)
    home_defensive_efficiency = _efficiency_per_100(home_scoring["avg_points_allowed"], home_possessions)
    away_offensive_efficiency = _efficiency_per_100(away_scoring["avg_points_scored"], away_possessions)
    away_defensive_efficiency = _efficiency_per_100(away_scoring["avg_points_allowed"], away_possessions)

    home_field_goal_pct = _rate(home_box_stats, "avg_field_goals_made", "avg_field_goal_attempts")
    away_field_goal_pct = _rate(away_box_stats, "avg_field_goals_made", "avg_field_goal_attempts")
    home_three_point_pct = _rate(home_box_stats, "avg_three_pointers_made", "avg_three_point_attempts")
    away_three_point_pct = _rate(away_box_stats, "avg_three_pointers_made", "avg_three_point_attempts")
    home_free_throw_pct = _rate(home_box_stats, "avg_free_throws_made", "avg_free_throw_attempts")
    away_free_throw_pct = _rate(away_box_stats, "avg_free_throws_made", "avg_free_throw_attempts")

    home_win_streak = current_streak(home_team_events, home_id)
    away_win_streak = current_streak(away_team_events, away_id)

    return {
        "event_key": event["event_key"],
        "event_date": event["event_date"],
        "home_entity_id": home_id,
        "away_entity_id": away_id,
        "kickoff_hour_utc": kickoff_hour_utc(event.get("kickoff_time")),
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
        "home_avg_rebounds": home_box_stats.get("avg_rebounds"),
        "home_avg_offensive_rebounds": home_box_stats.get("avg_offensive_rebounds"),
        "home_avg_defensive_rebounds": home_box_stats.get("avg_defensive_rebounds"),
        "home_avg_assists": home_box_stats.get("avg_assists"),
        "home_avg_steals": home_box_stats.get("avg_steals"),
        "home_avg_blocks": home_box_stats.get("avg_blocks"),
        "home_avg_turnovers": home_box_stats.get("avg_turnovers"),
        "home_avg_fouls": home_box_stats.get("avg_fouls"),
        "home_field_goal_pct": home_field_goal_pct,
        "home_three_point_pct": home_three_point_pct,
        "home_free_throw_pct": home_free_throw_pct,
        "home_offensive_efficiency": home_offensive_efficiency,
        "home_defensive_efficiency": home_defensive_efficiency,
        "home_box_games_played": home_box_stats["games_played"],
        "away_avg_rebounds": away_box_stats.get("avg_rebounds"),
        "away_avg_offensive_rebounds": away_box_stats.get("avg_offensive_rebounds"),
        "away_avg_defensive_rebounds": away_box_stats.get("avg_defensive_rebounds"),
        "away_avg_assists": away_box_stats.get("avg_assists"),
        "away_avg_steals": away_box_stats.get("avg_steals"),
        "away_avg_blocks": away_box_stats.get("avg_blocks"),
        "away_avg_turnovers": away_box_stats.get("avg_turnovers"),
        "away_avg_fouls": away_box_stats.get("avg_fouls"),
        "away_field_goal_pct": away_field_goal_pct,
        "away_three_point_pct": away_three_point_pct,
        "away_free_throw_pct": away_free_throw_pct,
        "away_offensive_efficiency": away_offensive_efficiency,
        "away_defensive_efficiency": away_defensive_efficiency,
        "away_box_games_played": away_box_stats["games_played"],
        "is_conference_game": is_conference_game(event),
        "home_win_streak": home_win_streak,
        "away_win_streak": away_win_streak,
        "home_team_injury_count": _team_injury_count(event.get("home_injuries")),
        "away_team_injury_count": _team_injury_count(event.get("away_injuries")),
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
    window: int = DEFAULT_ROLLING_WINDOW,
) -> dict:
    """One training row for a player-prop target -- same shape as
    library.features.nba.build_player_features (see its own docstring for
    why there's no season_type/week columns)."""
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
        "kickoff_hour_utc": kickoff_hour_utc(event.get("kickoff_time")),
        "rest_days": rest_days(player_game["event_date"], own_previous_event_date),
        "own_elo": own_elo,
        "opponent_elo": opponent_elo,
        "elo_diff": (own_elo - opponent_elo) if own_elo is not None and opponent_elo is not None else None,
        "is_conference_game": is_conference_game(event),
        "label_stat_line": player_game.get("stat_line", {}),
        "label_started": player_game.get("started"),
    }


def build_team_week_features(
    team_id: str,
    as_of_date: str,
    season: int,
    own_elo: float | None,
    team_season_events: list[dict],
    elo_ratings: dict[str, dict[str, float]],
    current_rank: int | None,
) -> dict:
    """One team-poll-week training row for the National Ranking model --
    team_id's own season-to-date state as of `as_of_date` (an AP poll's
    own release date), labeled with that poll's rank for this team if
    ranked.

    Poll-centric, not event-centric, unlike NCAAFB's own
    build_team_week_features -- NCAA MBB's AP polls aren't attached to
    individual events the way CFBD's rank data is (rankings live in their
    own S3 prefix, joined at feature-engineering time -- see
    project-ncaambb-onboarding memory for the full storage-design
    reasoning), so this is called once per (team, poll) rather than once
    per (team, event).

    own_elo is pre-resolved by the caller (feature-engineering's own
    build_dataset.py) rather than looked up here via elo_ratings plus a
    specific event_key, since there's no single event this row pins to --
    the caller's own resolution strategy is that module's concern, not
    this pure function's.

    team_season_events must already be scoped to team_id's own games
    within the SAME SEASON as as_of_date, strictly before it, most recent
    first -- record/scoring/streak/strength-of-schedule are season-to-date
    figures, not a trailing N-game rolling window, same reasoning as
    NCAAFB's own build_team_week_features.

    current_rank is None (excluded from training by
    train_ranking_model.py) for an unranked team-poll, same "missing, not
    fabricated" discipline as every other sparse-optional label in this
    project.
    """
    scoring = rolling_team_scoring_averages(team_season_events, team_id, window=len(team_season_events))
    wins, losses = _season_record(team_season_events, team_id)

    return {
        "team_id": team_id,
        "as_of_date": as_of_date,
        "season": season,
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
