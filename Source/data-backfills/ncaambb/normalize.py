"""
NCAA MBB-specific normalization: thin wrappers over library.normalize.espn
that bind the sport string and compound stat-key map so callers get a
simple single-argument API.

Player entities during backfill come from box scores only, via
boxscore_to_player_game_stats; there is no roster-based entity seeding.

_COMPOUND_KEY_SPLITS uses the exact same raw ESPN stat keys as NBA's own,
but stays its own dict rather than an import from nba's normalize
module, per this project's per-sport duplication convention.
"""
from library.normalize.espn import (
    team_to_entity as _team_to_entity,
    scoreboard_event_to_event_item as _scoreboard_event_to_event_item,
    boxscore_to_player_game_stats as _boxscore_to_player_game_stats,
    boxscore_to_team_game_stats as _boxscore_to_team_game_stats,
)

SPORT = "ncaambb"

# Both team- and player-level box scores use identical compound-key strings.
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
