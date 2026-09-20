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
from library.features import common
from library.features import nba_teams
from library.features.common import DEFAULT_ROLLING_WINDOW


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


def _event_extra_fields(event: dict, home_id: str, away_id: str) -> dict:
    home_travel_km, away_travel_km = nba_teams.travel_distances_km(away_id, home_id, event.get("venue_city"))
    return {
        "is_divisional_game": nba_teams.is_divisional_game(home_id, away_id),
        "is_international_game": nba_teams.is_international_game(event.get("venue_city")),
        "home_travel_km": home_travel_km,
        "away_travel_km": away_travel_km,
    }


def _player_extra_fields(event: dict, home_id: str, away_id: str, is_home: bool) -> dict:
    home_travel_km, away_travel_km = nba_teams.travel_distances_km(away_id, home_id, event.get("venue_city"))
    return {
        "is_divisional_game": nba_teams.is_divisional_game(home_id, away_id),
        "is_international_game": nba_teams.is_international_game(event.get("venue_city")),
        "travel_km": home_travel_km if is_home else away_travel_km,
    }


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
    return common.build_basketball_event_features(
        event, elo_ratings, home_team_events, away_team_events, window,
        home_team_box_stats, away_team_box_stats,
        rebounds_fn=_total_rebounds, extra_fields_fn=_event_extra_fields,
    )


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
    return common.build_basketball_player_features(
        player_game, prior_games, event, elo_ratings, own_previous_event_date, window,
        extra_fields_fn=_player_extra_fields,
    )
