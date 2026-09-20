"""
Pure NCAA MBB feature-computation functions -- the ESPN-sourced,
basketball equivalent of library.features.nba. Same split as every other
sport's feature module: no AWS calls here, every function takes
already-fetched rows and returns numbers, and train-time
(Source/feature-engineering/ncaambb/build_dataset.py) and live-serve-time
feature computation share these same functions.

Genuinely different from NBA, not just renamed:
- avg_total_rebounds is read directly off box_stats, not derived from
  offensive+defensive the way NBA's own _total_rebounds works around a
  data gap NBA's box score has and NCAA MBB's doesn't. ESPN's NCAA MBB
  box score carries a raw combined rebounds stat alongside
  offensiveRebounds/defensiveRebounds, labeled "Total Rebounds"
  (snake-cased to total_rebounds, not "rebounds").
- is_conference_game replaces NBA's is_divisional_game -- ESPN's own
  conferenceCompetition flag (library/normalize/espn.py's
  scoreboard_event_to_event_item), true for both a regular-season
  conference game and a conference-tournament game. No static
  division/conference table needed, unlike NBA's
  library.features.nba_teams -- yearly conference realignment makes a
  hand-maintained table the wrong tool here.
- No travel_km/is_international_game -- ESPN has no geo-coordinates
  anywhere for NCAA MBB teams/venues (checked both the site API's team
  resource and the core API's team/venue resources -- only city/state
  text, never lat/long), unlike NCAAFB's CFBD source (which embeds each
  team's home-stadium coordinates directly) or NBA's own static 30-team
  table.
- No National Ranking model features here -- build_team_week_features
  (the AP-poll-labeled national-ranking model's own feature builder) is
  separate; build_event_features/build_player_features below are the 4
  core training targets (win-probability, score margin/home/away, 6
  player-props) shared by every sport.
- No coach-tenure features, no venue_indoor -- same reasoning as NBA's
  own docstring (no coach data source; every arena is indoor, so the
  field would be a constant with no discriminating value).
- home_team_injury_count/away_team_injury_count only, no per-player
  injury-status equivalent -- same "no single dominant position the way
  a starting QB is" reasoning as NBA's own docstring. Sourced from
  aws-lambdas/ncaambb/ingest/handler.py's _attach_injuries -- NCAA MBB's
  roster response embeds injuries the same shape as NBA's.
- No season_type/week columns in build_player_features -- NCAA MBB's
  schedule is date-based, not week-based, same as NBA's.
"""
from library.features import common
from library.features.common import (
    DEFAULT_ROLLING_WINDOW,
    _season_record,
    average_opponent_elo,
    current_streak,
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


def _event_extra_fields(event: dict, home_id: str, away_id: str) -> dict:
    return {"is_conference_game": is_conference_game(event)}


def _player_extra_fields(event: dict, home_id: str, away_id: str, is_home: bool) -> dict:
    return {"is_conference_game": is_conference_game(event)}


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
    return common.build_basketball_event_features(
        event, elo_ratings, home_team_events, away_team_events, window,
        home_team_box_stats, away_team_box_stats,
        rebounds_fn=lambda box_stats: box_stats.get("avg_total_rebounds"), extra_fields_fn=_event_extra_fields,
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
    library.features.nba.build_player_features (see its own docstring for
    why there's no season_type/week columns)."""
    return common.build_basketball_player_features(
        player_game, prior_games, event, elo_ratings, own_previous_event_date, window,
        extra_fields_fn=_player_extra_fields,
    )


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
