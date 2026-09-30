"""
Unit tests for library.normalize.espn_handler.EspnNormalizer -- the
normalize Lambda body NFL/NBA/NCAA MBB share. The payload parsers
themselves are covered by test_espn_*.py.
"""
import logging
from unittest.mock import MagicMock, patch

from library.normalize import espn_handler

_PLAYER_SPLITS = {"a-b": ("a", "b")}
_TEAM_SPLITS = {"c-d": ("c", "d")}


def _normalizer(storage):
    return espn_handler.EspnNormalizer(
        "nba", lambda: storage, logging.getLogger("test-normalize"),
        player_compound_key_splits=_PLAYER_SPLITS, team_compound_key_splits=_TEAM_SPLITS,
    )


def test_teams_and_scoreboard_upsert_every_entry():
    storage = MagicMock()
    normalizer = _normalizer(storage)

    with patch.object(espn_handler, "team_to_entity", side_effect=lambda team, sport: {"team": team["id"]}), \
            patch.object(espn_handler, "scoreboard_event_to_event_item", side_effect=lambda event, sport: {"event": event["id"]}):
        normalizer.process_teams({"sports": [{"leagues": [{"teams": [{"team": {"id": "1"}}, {"team": {"id": "2"}}]}]}]}, "k")
        normalizer.process_scoreboard({"events": [{"id": "e1"}]}, "k")

    assert [c.args[0] for c in storage.upsert_entity.call_args_list] == [{"team": "1"}, {"team": "2"}]
    storage.upsert_event.assert_called_once_with({"event": "e1"})


def test_boxscore_uses_the_sports_own_compound_key_splits():
    storage = MagicMock()

    with patch.object(espn_handler, "boxscore_to_player_game_stats", return_value=(["stat"], [{"entity_id": "p1"}])) as players, \
            patch.object(espn_handler, "boxscore_to_team_game_stats", return_value=["team-stat"]) as teams:
        _normalizer(storage).process_boxscore({"box": 1}, "k")

    players.assert_called_once_with({"box": 1}, "nba", _PLAYER_SPLITS)
    teams.assert_called_once_with({"box": 1}, "nba", _TEAM_SPLITS)
    storage.upsert_player_entity.assert_called_once_with({"entity_id": "p1"})
    storage.write_team_game_stats.assert_called_once_with(["team-stat"])


def test_roster_clears_players_no_longer_listed():
    storage = MagicMock()
    storage.get_team_entities.return_value = [
        {"entity_id": "stays"},
        {"entity_id": "left", "team_key": "TEAM#1", "metadata": {"team_id": "1", "position": "G"}},
        {"metadata": {}},
    ]
    payload = {"team": {"id": 1}, "timestamp": "2026-09-29T12:00:00Z"}

    with patch.object(espn_handler, "roster_to_player_entities", return_value=[{"entity_id": "stays"}]):
        _normalizer(storage).process_roster(payload, "k")

    cleared = storage.upsert_player_entity.call_args_list[-1].args[0]
    assert cleared == {"entity_id": "left", "metadata": {"position": "G", "team_id_as_of": "2026-09-29"}}
    storage.get_team_entities.assert_called_once_with("nba", "1")


def test_an_empty_roster_clears_nobody():
    storage = MagicMock()

    assert _normalizer(storage).clear_departed_players(storage, "1", [], "2026-09-29") == 0
    storage.get_team_entities.assert_not_called()


def test_dispatch_routes_through_the_shared_dispatcher():
    normalizer = _normalizer(MagicMock())

    with patch.object(espn_handler.dispatch_common, "dispatch") as dispatch:
        normalizer.dispatch("s3", "bucket", "nba/teams.json")

    dispatch.assert_called_once()
    assert dispatch.call_args.args[:3] == ("s3", "bucket", "nba/teams.json")
    assert dispatch.call_args.kwargs["process_roster"] == normalizer.process_roster
