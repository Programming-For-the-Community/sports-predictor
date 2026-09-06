"""
Unit tests for the NCAA MBB normalize Lambda handler. All AWS calls are
mocked. Tests verify key routing via _dispatch, that each processor calls
the right storage methods the right number of times, and that the
handler is resilient to individual record failures. NCAA MBB's
normalizers are the shared library.normalize.espn functions (see
handler.py's own docstring for why there's no separate
library/normalize/ncaambb.py) -- these tests exercise the dispatch/wiring,
not the normalizer logic itself, which is covered by
tests/library/normalize/test_espn_*.py.

The ncaambb_normalize module is registered in sys.modules by conftest.py.
"""
import json

import pytest
from unittest.mock import MagicMock, patch

import ncaambb_normalize


@pytest.fixture(autouse=True)
def reset_storage():
    ncaambb_normalize._storage = None
    yield
    ncaambb_normalize._storage = None


def _s3_response(payload) -> dict:
    body = MagicMock()
    body.read.return_value = json.dumps(payload).encode()
    return {"Body": body}


def _s3_record(bucket: str, key: str) -> dict:
    return {"s3": {"bucket": {"name": bucket}, "object": {"key": key}}}


class TestDispatch:
    def test_routes_teams_key_to_teams_processor(self):
        payload = {"sports": [{"leagues": [{"teams": [{"team": {"id": "1"}}, {"team": {"id": "2"}}]}]}]}
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage = MagicMock()

        with patch.object(ncaambb_normalize, "_s3", mock_s3), \
             patch("ncaambb_normalize.PipelineStorage", return_value=mock_storage):
            ncaambb_normalize._dispatch("test-bucket", "ncaambb/teams.json")

        assert mock_storage.upsert_entity.call_count == 2

    def test_routes_scoreboard_key_to_scoreboard_processor(self):
        payload = {"events": [{"id": "1"}, {"id": "2"}]}
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage = MagicMock()
        event_stub = {"event_key": "stub"}

        with patch.object(ncaambb_normalize, "_s3", mock_s3), \
             patch("ncaambb_normalize.PipelineStorage", return_value=mock_storage), \
             patch.object(ncaambb_normalize, "scoreboard_event_to_event_item", return_value=event_stub):
            ncaambb_normalize._dispatch("test-bucket", "ncaambb/scoreboard/20260114.json")

        assert mock_storage.upsert_event.call_count == 2

    def test_routes_boxscore_key_to_boxscore_processor(self):
        payload = {"header": {"id": "1"}, "boxscore": {"players": [], "teams": []}}
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage = MagicMock()

        with patch.object(ncaambb_normalize, "_s3", mock_s3), \
             patch("ncaambb_normalize.PipelineStorage", return_value=mock_storage):
            ncaambb_normalize._dispatch("test-bucket", "ncaambb/boxscore/2026/1.json")

        mock_storage.write_player_game_stats.assert_called_once()
        mock_storage.write_team_game_stats.assert_called_once()

    def test_routes_roster_key_to_roster_processor(self):
        payload = {"team": {"id": "1"}, "timestamp": "2026-01-01T00:00Z", "athletes": [{"id": "a1"}, {"id": "a2"}]}
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage = MagicMock()

        with patch.object(ncaambb_normalize, "_s3", mock_s3), \
             patch("ncaambb_normalize.PipelineStorage", return_value=mock_storage):
            ncaambb_normalize._dispatch("test-bucket", "ncaambb/roster/1.json")

        assert mock_storage.upsert_player_entity.call_count == 2

    def test_unrecognized_key_is_skipped_without_raising(self):
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response({})

        with patch.object(ncaambb_normalize, "_s3", mock_s3), \
             patch("ncaambb_normalize.PipelineStorage"):
            ncaambb_normalize._dispatch("test-bucket", "ncaambb/unknown/thing.json")  # must not raise


class TestClearDepartedPlayers:
    def test_clears_a_player_missing_from_the_fresh_roster(self):
        # Regression: a player who's since left the team (transferred,
        # graduated, gone pro) but is simply absent from a fresh ESPN
        # roster fetch previously kept whatever team_id their last
        # confirmation set, forever -- roster_to_player_entities' own
        # upserts only ever add/refresh players actually present in the
        # new payload, never remove one who's disappeared from it. Same
        # fix as ncaafb/normalize/handler.py's own _clear_departed_players.
        storage = MagicMock()
        storage.get_team_entities.return_value = [
            {"entity_id": "a1", "name": "Still Here", "metadata": {"team_id": "1", "team_id_as_of": "2026-08-12"}},
            {"entity_id": "a2", "name": "Departed Player", "team_key": "SPORT#NCAAMBB#TEAM#1",
             "metadata": {"team_id": "1", "team_id_as_of": "2026-08-12", "position": "G"}},
        ]
        storage.upsert_player_entity.return_value = True
        entities = [{"entity_id": "a1", "metadata": {"team_id": "1"}}]  # only a1 is in the fresh roster

        cleared = ncaambb_normalize._clear_departed_players(storage, "1", entities, "2026-09-06")

        assert cleared == 1
        storage.get_team_entities.assert_called_once_with("ncaambb", "1")
        written = storage.upsert_player_entity.call_args.args[0]
        assert written["entity_id"] == "a2"
        assert "team_key" not in written  # dropped out of the team-index GSI entirely
        assert "team_id" not in written["metadata"]
        assert written["metadata"]["team_id_as_of"] == "2026-09-06"

    def test_does_not_touch_a_player_still_present_in_the_fresh_roster(self):
        storage = MagicMock()
        storage.get_team_entities.return_value = [
            {"entity_id": "a1", "metadata": {"team_id": "1", "team_id_as_of": "2026-08-12"}},
        ]
        entities = [{"entity_id": "a1", "metadata": {"team_id": "1"}}]

        cleared = ncaambb_normalize._clear_departed_players(storage, "1", entities, "2026-09-06")

        assert cleared == 0
        storage.upsert_player_entity.assert_not_called()

    def test_a_genuinely_empty_fetch_is_left_alone_rather_than_wiping_the_whole_team(self):
        # NCAA MBB re-fetches every team's roster daily -- a transient
        # ESPN API gap for this one team is far more likely than every
        # player really leaving at once, so an empty fetch is a no-op
        # that self-heals on tomorrow's re-fetch instead of wiping the
        # team's whole roster attribution over one bad response.
        storage = MagicMock()

        cleared = ncaambb_normalize._clear_departed_players(storage, "1", [], "2026-09-06")

        assert cleared == 0
        storage.get_team_entities.assert_not_called()


class TestLambdaHandler:
    def test_processes_every_record_independently(self):
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response({"events": []})
        event = {"Records": [
            _s3_record("test-bucket", "ncaambb/scoreboard/20260114.json"),
            _s3_record("test-bucket", "ncaambb/scoreboard/20260115.json"),
        ]}

        with patch.object(ncaambb_normalize, "_s3", mock_s3), \
             patch("ncaambb_normalize.PipelineStorage"):
            result = ncaambb_normalize.lambda_handler(event, None)

        assert result == {"processed": 2, "failed": 0}

    def test_one_records_failure_does_not_block_the_others(self):
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = [Exception("S3 error"), _s3_response({"events": []})]
        event = {"Records": [
            _s3_record("test-bucket", "ncaambb/scoreboard/20260114.json"),
            _s3_record("test-bucket", "ncaambb/scoreboard/20260115.json"),
        ]}

        with patch.object(ncaambb_normalize, "_s3", mock_s3), \
             patch("ncaambb_normalize.PipelineStorage"):
            result = ncaambb_normalize.lambda_handler(event, None)

        assert result == {"processed": 1, "failed": 1}
