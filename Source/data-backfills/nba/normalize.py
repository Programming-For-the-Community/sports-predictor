"""
NBA-specific normalization: thin wrappers over library.normalize.espn that
bind the sport string and compound stat-key map so callers get the same
simple single-argument API regardless of which shared function does the work.

Player entities during backfill come from box scores only (via
boxscore_to_player_game_stats), same as NFL's own normalize.py wrapper --
there's no roster-based entity seeding here, since a CURRENT roster fetch
(aws-lambdas/nba/ingest/handler.py's own daily _fetch_rosters) has nothing
meaningful to say about a historical season's roster.
"""
from library.normalize.espn import (
    team_to_entity as _team_to_entity,
    scoreboard_event_to_event_item as _scoreboard_event_to_event_item,
    boxscore_to_player_game_stats as _boxscore_to_player_game_stats,
    boxscore_to_team_game_stats as _boxscore_to_team_game_stats,
)

SPORT = "nba"

# Same single shared map (both team- and player-level box scores use
# identical compound-key strings) as aws-lambdas/nba/normalize/handler.py's
# own _COMPOUND_KEY_SPLITS -- duplicated here rather than imported across
# packages, same as NFL's own normalize.py wrapper duplicates
# aws-lambdas/nfl/normalize/handler.py's map (the backfill Docker image
# only installs library/, not aws-lambdas/ -- see this directory's own
# Dockerfile).
_COMPOUND_KEY_SPLITS: dict[str, tuple[str, str]] = {
    "fieldGoalsMade-fieldGoalsAttempted": ("field_goals_made", "field_goal_attempts"),
    "threePointFieldGoalsMade-threePointFieldGoalsAttempted": ("three_pointers_made", "three_point_attempts"),
    "freeThrowsMade-freeThrowsAttempted": ("free_throws_made", "free_throw_attempts"),
}


def team_to_entity(team: dict) -> dict:
    return _team_to_entity(team, SPORT)


def scoreboard_event_to_event_item(event: dict) -> dict:
    return _scoreboard_event_to_event_item(event, SPORT)


def boxscore_to_player_game_stats(summary: dict) -> tuple[list[dict], list[dict]]:
    return _boxscore_to_player_game_stats(summary, SPORT, _COMPOUND_KEY_SPLITS)


def boxscore_to_team_game_stats(summary: dict) -> list[dict]:
    return _boxscore_to_team_game_stats(summary, SPORT, _COMPOUND_KEY_SPLITS)
