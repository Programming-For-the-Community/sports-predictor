"""
Unit tests for the NHL normalize Lambda handler. All AWS calls are
mocked. Tests verify key routing via _dispatch, that scoreboards and box
scores go through library.normalize.nhl with the preseason/exhibition
filter applied, and that the handler is resilient to individual record
failures. The normalizer logic itself is covered by
tests/library/normalize/test_nhl_normalize.py.

The nhl_normalize module is registered in sys.modules by conftest.py.
"""
import json
from unittest.mock import MagicMock, patch

import nhl_normalize


def _s3_with(payload) -> MagicMock:
    body = MagicMock()
    body.read.return_value = json.dumps(payload).encode()
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": body}
    return s3


def _s3_record(bucket: str, key: str) -> dict:
    return {"s3": {"bucket": {"name": bucket}, "object": {"key": key}}}


def _event(event_id, season_type=2, home_id="13", away_id="2"):
    return {
        "id": event_id, "season": {"year": 2027, "type": season_type},
        "competitions": [{"competitors": [{"team": {"id": home_id}}, {"team": {"id": away_id}}]}],
    }


def _summary(home_id="13", away_id="2"):
    return {"header": {"id": "1", "competitions": [{"competitors": [{"team": {"id": home_id}}, {"team": {"id": away_id}}]}]}}


def _dispatch(payload, key):
    storage = MagicMock()
    with patch.object(nhl_normalize, "_s3", _s3_with(payload)), \
         patch("nhl_normalize.PipelineStorage", return_value=storage):
        nhl_normalize._dispatch("test-bucket", key)
    return storage


class TestDispatch:
    def test_teams_key_upserts_a_team_entity_per_team(self):
        payload = {"sports": [{"leagues": [{"teams": [{"team": {"id": "1"}}, {"team": {"id": "2"}}]}]}]}

        storage = _dispatch(payload, "nhl/teams.json")

        assert storage.upsert_entity.call_count == 2
        assert storage.upsert_entity.call_args_list[0].args[0]["entity_key"] == "SPORT#NHL#ENTITY#TEAM#1"

    def test_scoreboard_key_upserts_only_ingestable_events_through_the_nhl_normalizer(self):
        payload = {"events": [
            _event("1"), _event("2", season_type=1), _event("3", home_id="129030", away_id="129031"), _event("4"),
        ]}

        with patch.object(nhl_normalize.nhl, "scoreboard_event_to_event_item", side_effect=lambda e, sport: {"id": e["id"], "sport": sport}):
            storage = _dispatch(payload, "nhl/scoreboard/20261009.json")

        assert [c.args[0] for c in storage.upsert_event.call_args_list] == [
            {"id": "1", "sport": "nhl"}, {"id": "4", "sport": "nhl"},
        ]

    def test_boxscore_key_writes_player_and_team_stats_through_the_nhl_normalizer(self):
        entities = [{"entity_id": "p1"}, {"entity_id": "p2"}]

        with patch.object(nhl_normalize.nhl, "boxscore_to_player_game_stats", return_value=([{"row": 1}], entities)) as player_stats, \
             patch.object(nhl_normalize.nhl, "boxscore_to_team_game_stats", return_value=[{"team": 1}]):
            storage = _dispatch(_summary(), "nhl/boxscore/2027/1.json")

        assert player_stats.call_args.args[1] == "nhl"
        assert [c.args[0] for c in storage.upsert_player_entity.call_args_list] == entities
        storage.write_player_game_stats.assert_called_once_with([{"row": 1}])
        storage.write_team_game_stats.assert_called_once_with([{"team": 1}])

    def test_non_franchise_boxscore_is_skipped(self):
        storage = _dispatch(_summary(home_id="129030", away_id="129031"), "nhl/boxscore/2024/9.json")

        storage.upsert_player_entity.assert_not_called()
        storage.write_player_game_stats.assert_not_called()
        storage.write_team_game_stats.assert_not_called()

    def test_roster_key_upserts_player_entities_from_grouped_athletes(self):
        payload = {
            "team": {"id": "13"}, "timestamp": "2026-10-09T21:31:12Z",
            "athletes": [
                {"position": "Centers", "items": [{"id": "a1", "displayName": "A", "position": {"abbreviation": "C"}}]},
                {"position": "Goalies", "items": [{"id": "a2", "displayName": "B", "position": {"abbreviation": "G"}}]},
            ],
        }

        with patch.object(nhl_normalize._normalizer, "clear_departed_players", return_value=0):
            storage = _dispatch(payload, "nhl/roster/13.json")

        written = [c.args[0] for c in storage.upsert_player_entity.call_args_list]
        assert [(e["entity_id"], e["metadata"]["position"], e["metadata"]["team_id"]) for e in written] == [
            ("a1", "C", "13"), ("a2", "G", "13"),
        ]

    def test_unrecognized_key_is_ignored(self):
        storage = _dispatch({}, "nhl/backfill-failures/20261009T000000Z.json")

        assert storage.method_calls == []


class TestLambdaHandler:
    def test_one_failing_record_does_not_block_the_others(self):
        event = {"Records": [_s3_record("b", "nhl/teams.json"), _s3_record("b", "nhl/scoreboard/20261009.json")]}

        with patch.object(nhl_normalize, "_dispatch", side_effect=[Exception("boom"), None]):
            result = nhl_normalize.lambda_handler(event, None)

        assert result == {"processed": 1, "failed": 1}
