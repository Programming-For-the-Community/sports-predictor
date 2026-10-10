"""
NHL-specific normalization: thin wrappers binding the sport string onto
library.normalize.espn's team normalizer and library.normalize.nhl's
hockey event/box-score normalizers, so callers get a single-argument API.

Player entities during backfill come from box scores only; there is no
roster-based entity seeding.
"""
from library.normalize import nhl
from library.normalize.espn import team_to_entity as _team_to_entity

SPORT = "nhl"


def team_to_entity(team: dict) -> dict:
    return _team_to_entity(team, SPORT)


def scoreboard_event_to_event_item(event: dict) -> dict:
    return nhl.scoreboard_event_to_event_item(event, SPORT)


def boxscore_to_player_game_stats(summary: dict) -> tuple[list[dict], list[dict]]:
    return nhl.boxscore_to_player_game_stats(summary, SPORT)


def boxscore_to_team_game_stats(summary: dict) -> list[dict]:
    return nhl.boxscore_to_team_game_stats(summary, SPORT)
