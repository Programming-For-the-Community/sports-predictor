"""
NFL-specific normalization: thin wrappers over library.normalize.espn that
bind the sport string and compound stat-key map so callers get the same
simple single-argument API regardless of which shared function does the work.
"""
from library.normalize.espn import (
    team_to_entity as _team_to_entity,
    scoreboard_event_to_event_item as _scoreboard_event_to_event_item,
    boxscore_to_player_game_stats as _boxscore_to_player_game_stats,
    boxscore_to_team_game_stats as _boxscore_to_team_game_stats,
)

SPORT = "nfl"

# ESPN box score stat keys that pack two numbers into one string.
_COMPOUND_KEY_SPLITS: dict[str, tuple[str, str]] = {
    "completions/passingAttempts": ("completions", "passing_attempts"),
    "sacks-sackYardsLost": ("sacks_taken", "sack_yards_lost"),
    "fieldGoalsMade/fieldGoalAttempts": ("field_goals_made", "field_goal_attempts"),
    "extraPointsMade/extraPointAttempts": ("extra_points_made", "extra_point_attempts"),
}

_TEAM_COMPOUND_KEY_SPLITS: dict[str, tuple[str, str]] = {
    "thirdDownEff": ("third_down_conversions", "third_down_attempts"),
    "fourthDownEff": ("fourth_down_conversions", "fourth_down_attempts"),
    "completionAttempts": ("completions", "pass_attempts"),
    "redZoneAttempts": ("red_zone_conversions", "red_zone_attempts"),
    "sacksYardsLost": ("sacks_taken", "sack_yards_lost"),
    "totalPenaltiesYards": ("penalties", "penalty_yards"),
}


def team_to_entity(team: dict) -> dict:
    return _team_to_entity(team, SPORT)


def scoreboard_event_to_event_item(event: dict) -> dict:
    return _scoreboard_event_to_event_item(event, SPORT)


def boxscore_to_player_game_stats(summary: dict) -> tuple[list[dict], list[dict]]:
    return _boxscore_to_player_game_stats(summary, SPORT, _COMPOUND_KEY_SPLITS)


def boxscore_to_team_game_stats(summary: dict) -> list[dict]:
    return _boxscore_to_team_game_stats(summary, SPORT, _TEAM_COMPOUND_KEY_SPLITS)