"""
Pure NBA feature-computation functions -- the ESPN-sourced, basketball
equivalent of library.features.nfl/library.features.ncaafb. Same split as
both: no AWS calls here, every function takes already-fetched rows and
returns numbers, and train-time (Source/feature-engineering/nba/
build_dataset.py) and live-serve-time feature computation are meant to
share these same functions once inference lands (step 6) -- see
library.features.nfl's own docstring for why that matters.

Genuinely different from NFL/NCAAFB, not just renamed:
- No QB/RB/WR-equivalent position exists in basketball -- there's no
  single player whose own performance dominates a team's outcome the way
  a starting QB's does, so build_event_features carries no
  identify-a-leader-then-track-their-history sub-features the way NFL's/
  NCAAFB's own build_event_features do. Team-level rolling box-score
  averages (shooting splits, rebounds, turnovers, efficiency -- see
  below) carry that signal instead. Individual players ARE modeled, just
  as their own dedicated player-prop rows (build_player_features), same
  as every other sport.
- home_team_injury_count/away_team_injury_count only -- no
  home_qb_injury_status equivalent, since there's no starting-QB-style
  single player whose own status matters more than the team's overall
  count (see this docstring's own note above on why no leader-tracking
  sub-features exist here at all). Sourced from Source/aws-lambdas/nba/
  ingest/handler.py's _attach_injuries, which attaches each event's
  home_injuries/away_injuries from that SAME run's roster fetch (NBA's
  roster response embeds injuries directly, so unlike NFL this needs no
  separate injury-report API call). Forward-only, same
  as NFL's own coach/injury fields: only populated for events an ingest
  run has actually enriched, null on anything backfilled before this
  shipped. The exact raw per-athlete injury status field name is
  UNVERIFIED against a real payload -- see library/normalize/espn.py's
  roster_to_team_injuries docstring.
- No coach-tenure features -- explicitly deferred, out of Sub-phase 3A's
  scope (see project-nba-onboarding memory); NBA has no coach data source
  wired in the way NFL's separate "core" API client provides one.
- offensive_efficiency/defensive_efficiency are new: basketball has no
  NFL/NCAAFB analog to a possessions-per-100 pace metric. Derived (not
  raw) from a team's own rolling shot-volume/turnover/rebound averages.
  estimate_possessions/_efficiency_per_100 (the Dean Oliver math itself)
  moved to library.features.common 2026-08-20 -- sport-agnostic
  basketball formulas, not an NBA-only concept (NCAA MBB's own feature
  module needs the identical formula) -- imported from there now rather
  than defined here.
- is_divisional_game/is_international_game/travel_distances_km come from
  library.features.nba_teams' own static tables (NBA is a fixed 30
  franchises, low realignment risk -- closer to NFL's hardcoded-table
  precedent than NCAAFB's CFBD-sourced dynamic per-season lookup, see
  that module's own docstring), so build_event_features/
  build_player_features take no team_coordinates parameter the way
  NCAAFB's own versions do.
- No National Ranking model / build_team_week_features equivalent -- NBA
  has no in-season poll (see dynamodb-sport-registry.tf's nba_registry,
  which has no national-ranking training target), same asymmetry NCAAFB
  already has relative to NFL, just the other direction.
- No venue_indoor -- every NBA arena is indoor, so the field NFL/NCAAFB
  carry to distinguish a dome game from an outdoor one (weather
  relevance) has no discriminating value here; every row would carry the
  same constant, contributing nothing a model could split on. Dropped
  outright rather than carried as an always-true column, same "don't
  feature-engineer something with no real signal" discipline as the
  injury/coach omissions above.
"""
from library.features import nba_teams
from library.features.common import (
    DEFAULT_ROLLING_WINDOW,
    _efficiency_per_100,
    _rate,
    _team_injury_count,
    current_streak,
    estimate_possessions,
    kickoff_hour_utc,
    rest_days,
    rolling_player_stat_averages,
    rolling_team_scoring_averages,
)


def _total_rebounds(box_stats: dict) -> float | None:
    """Total rebounds isn't its own ESPN team-boxscore stat (confirmed
    live via a real training run, 2026-08-15: 'avg_rebounds' came back
    null for every row) -- only offensiveRebounds/defensiveRebounds exist
    as raw stats. Derived as their sum instead of trusting a stat_line key
    that was never actually live-verified, same "derived, not raw"
    treatment as estimate_possessions."""
    offensive = box_stats.get("avg_offensive_rebounds")
    defensive = box_stats.get("avg_defensive_rebounds")
    if offensive is None or defensive is None:
        return None
    return offensive + defensive


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
    same convention as library.features.nfl.build_event_features.

    elo_ratings is compute_elo_ratings' full output, called by this
    module's own caller (build_dataset.py) with library.features.common's
    NFL-tuned default constants -- basketball's own tuned constants
    (higher-scoring games, different margin-of-victory behavior) are a
    known open item, deliberately not guessed here; see
    design/PROJECT_PLAN.md's Elo section. home_team_events/
    away_team_events are each team's own prior completed events, most
    recent first, NOT including this one.

    home_team_box_stats/away_team_box_stats are each team's own prior
    team_game_stats rows (shooting splits, rebounds, assists, turnovers,
    fouls -- see library/normalize/espn.py's boxscore_to_team_game_stats
    and Source/aws-lambdas/nba/normalize/handler.py's own
    _COMPOUND_KEY_SPLITS for the exact stat_line keys this reads), most
    recent first, NOT including this one. No home_qb_games-style
    per-position argument -- see this module's own docstring for why.
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

    home_travel_km, away_travel_km = nba_teams.travel_distances_km(away_id, home_id, event.get("venue_city"))

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
        "home_avg_rebounds": _total_rebounds(home_box_stats),
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
        "away_avg_rebounds": _total_rebounds(away_box_stats),
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
        "is_divisional_game": nba_teams.is_divisional_game(home_id, away_id),
        "is_international_game": nba_teams.is_international_game(event.get("venue_city")),
        "home_travel_km": home_travel_km,
        "away_travel_km": away_travel_km,
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
    library.features.nfl.build_player_features (see its own docstring).
    No season_type/week columns -- ESPN's NBA schedule has no week
    numbering the way NFL's/NCAAFB's does (see
    Source/data-backfills/nba/backfill.py's own date-walk, not a week
    walk), so there's nothing to carry through here that build_dataset.py
    even has on hand.
    """
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

    home_travel_km, away_travel_km = nba_teams.travel_distances_km(away_id, home_id, event.get("venue_city"))
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
        "kickoff_hour_utc": kickoff_hour_utc(event.get("kickoff_time")),
        "rest_days": rest_days(player_game["event_date"], own_previous_event_date),
        "own_elo": own_elo,
        "opponent_elo": opponent_elo,
        "elo_diff": (own_elo - opponent_elo) if own_elo is not None and opponent_elo is not None else None,
        "is_divisional_game": nba_teams.is_divisional_game(home_id, away_id),
        "is_international_game": nba_teams.is_international_game(event.get("venue_city")),
        "travel_km": own_travel_km,
        "label_stat_line": player_game.get("stat_line", {}),
        "label_started": player_game.get("started"),
    }
