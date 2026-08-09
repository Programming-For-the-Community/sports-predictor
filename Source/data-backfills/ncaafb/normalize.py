"""
NCAAFB-specific normalization: thin wrappers over library.normalize.ncaafb
that bind the sport string, mirroring data-backfills/nfl/normalize.py's
own role for library.normalize.espn.
"""
from library.normalize.ncaafb import (
    team_to_entity as _team_to_entity,
    game_to_event_item as _game_to_event_item,
    game_player_stats_to_player_game_stats as _game_player_stats_to_player_game_stats,
    game_team_stats_to_team_game_stats as _game_team_stats_to_team_game_stats,
)

SPORT = "ncaafb"


def team_to_entity(team: dict) -> dict:
    return _team_to_entity(team, SPORT)


def game_to_event_item(game: dict) -> dict:
    return _game_to_event_item(game, SPORT)


def game_player_stats_to_player_game_stats(game_box_score: dict) -> tuple[list[dict], list[dict]]:
    return _game_player_stats_to_player_game_stats(game_box_score, SPORT)


def game_team_stats_to_team_game_stats(game_team_box_score: dict) -> list[dict]:
    return _game_team_stats_to_team_game_stats(game_team_box_score, SPORT)
