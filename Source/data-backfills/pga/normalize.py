"""
PGA-specific normalization: thin wrappers over library.normalize.pga that
bind the sport string, same pattern as every other sport's backfill
normalize.py.
"""
from library.normalize.pga import (
    leaderboard_event_to_event_item as _leaderboard_event_to_event_item,
    leaderboard_event_to_player_entities as _leaderboard_event_to_player_entities,
)
from library.normalize.pga_matchplay import (
    leaderboard_event_to_cup_event_item as _leaderboard_event_to_cup_event_item,
    leaderboard_event_to_match_event_items as _leaderboard_event_to_match_event_items,
    leaderboard_event_to_matchplay_player_entities as _leaderboard_event_to_matchplay_player_entities,
    leaderboard_event_to_matchplay_team_entities as _leaderboard_event_to_matchplay_team_entities,
)

SPORT = "pga"


def leaderboard_event_to_event_item(event: dict) -> dict:
    return _leaderboard_event_to_event_item(event, SPORT)


def leaderboard_event_to_player_entities(event: dict) -> list[dict]:
    return _leaderboard_event_to_player_entities(event, SPORT)


def leaderboard_event_to_cup_event_item(event: dict) -> dict | None:
    return _leaderboard_event_to_cup_event_item(event, SPORT)


def leaderboard_event_to_match_event_items(event: dict) -> list[dict]:
    return _leaderboard_event_to_match_event_items(event, SPORT)


def leaderboard_event_to_matchplay_team_entities(event: dict) -> list[dict]:
    return _leaderboard_event_to_matchplay_team_entities(event, SPORT)


def leaderboard_event_to_matchplay_player_entities(event: dict) -> list[dict]:
    return _leaderboard_event_to_matchplay_player_entities(event, SPORT)
