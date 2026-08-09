"""
Unit tests for the NCAAFB normalize Lambda handler. All AWS calls are
mocked. Tests verify key routing via _dispatch (every CFBD raw object is
a JSON list, one entry per game -- unlike NFL's per-object single dict,
since CFBD's box score endpoints are bulk-per-week), that each processor
calls the right storage methods the right number of times, and that the
handler is resilient to individual record failures.

The ncaafb_normalize module is registered in sys.modules by conftest.py.
"""
import json

import pytest
from unittest.mock import MagicMock, patch

import ncaafb_normalize


@pytest.fixture(autouse=True)
def reset_storage():
    ncaafb_normalize._storage = None
    yield
    ncaafb_normalize._storage = None


def _s3_response(payload) -> dict:
    body = MagicMock()
    body.read.return_value = json.dumps(payload).encode()
    return {"Body": body}


def _s3_record(bucket: str, key: str) -> dict:
    return {"s3": {"bucket": {"name": bucket}, "object": {"key": key}}}


class TestDispatch:
    def test_routes_games_key_to_games_processor(self):
        payload = [{"id": "1"}, {"id": "2"}, {"id": "3"}]
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage = MagicMock()
        event_stub = {"pk": "ncaafb#event#1"}

        with patch.object(ncaafb_normalize, "_s3", mock_s3), \
             patch("ncaafb_normalize.PipelineStorage", return_value=mock_storage), \
             patch.object(ncaafb_normalize, "game_to_event_item", return_value=event_stub):
            ncaafb_normalize._dispatch("test-bucket", "ncaafb/games/2025/regular/4.json")

        assert mock_storage.upsert_event.call_count == 3

    def test_routes_boxscore_key_to_boxscore_processor(self):
        payload = [{"id": "1", "teams": []}, {"id": "2", "teams": []}]
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage = MagicMock()
        stats = [{"pk": "stat1"}]
        entities = [{"pk": "ncaafb#player#1"}]

        with patch.object(ncaafb_normalize, "_s3", mock_s3), \
             patch("ncaafb_normalize.PipelineStorage", return_value=mock_storage), \
             patch.object(ncaafb_normalize, "game_player_stats_to_player_game_stats", return_value=(stats, entities)):
            ncaafb_normalize._dispatch("test-bucket", "ncaafb/boxscore/2025/regular/4.json")

        assert mock_storage.upsert_player_entity.call_count == 2  # once per game entry
        assert mock_storage.write_player_game_stats.call_count == 2

    def test_routes_teamstats_key_to_teamstats_processor(self):
        payload = [{"id": "1", "teams": []}]
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage = MagicMock()
        team_stats = [{"pk": "team-stat1"}, {"pk": "team-stat2"}]

        with patch.object(ncaafb_normalize, "_s3", mock_s3), \
             patch("ncaafb_normalize.PipelineStorage", return_value=mock_storage), \
             patch.object(ncaafb_normalize, "game_team_stats_to_team_game_stats", return_value=team_stats):
            ncaafb_normalize._dispatch("test-bucket", "ncaafb/teamstats/2025/regular/4.json")

        mock_storage.write_team_game_stats.assert_called_once_with(team_stats)

    def test_ignores_unrecognized_key_without_raising(self):
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response([])
        mock_storage = MagicMock()

        with patch.object(ncaafb_normalize, "_s3", mock_s3), \
             patch("ncaafb_normalize.PipelineStorage", return_value=mock_storage):
            ncaafb_normalize._dispatch("test-bucket", "ncaafb/random/unknown.json")

        mock_storage.upsert_event.assert_not_called()
        mock_storage.upsert_player_entity.assert_not_called()
        mock_storage.write_player_game_stats.assert_not_called()
        mock_storage.write_team_game_stats.assert_not_called()

    def test_empty_games_list_does_not_call_upsert(self):
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response([])
        mock_storage = MagicMock()

        with patch.object(ncaafb_normalize, "_s3", mock_s3), \
             patch("ncaafb_normalize.PipelineStorage", return_value=mock_storage):
            ncaafb_normalize._dispatch("test-bucket", "ncaafb/games/2025/regular/4.json")

        mock_storage.upsert_event.assert_not_called()

    def test_s3_get_object_uses_correct_bucket_and_key(self):
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response([])

        with patch.object(ncaafb_normalize, "_s3", mock_s3), \
             patch("ncaafb_normalize.PipelineStorage"):
            ncaafb_normalize._dispatch("my-bucket", "ncaafb/games/2025/regular/4.json")

        mock_s3.get_object.assert_called_once_with(Bucket="my-bucket", Key="ncaafb/games/2025/regular/4.json")


class TestNormalizeLambdaHandler:
    def test_processes_multiple_records(self):
        payload = [{"id": "1"}]
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage = MagicMock()
        event = {"Records": [
            _s3_record("test-bucket", "ncaafb/games/2025/regular/4.json"),
            _s3_record("test-bucket", "ncaafb/games/2025/regular/5.json"),
        ]}

        with patch.object(ncaafb_normalize, "_s3", mock_s3), \
             patch("ncaafb_normalize.PipelineStorage", return_value=mock_storage), \
             patch.object(ncaafb_normalize, "game_to_event_item", return_value={"pk": "e"}):
            result = ncaafb_normalize.lambda_handler(event, None)

        assert result == {"processed": 2, "failed": 0}

    def test_continues_after_individual_record_failure(self):
        payload = [{"id": "1"}]
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = [Exception("S3 timeout"), _s3_response(payload)]
        mock_storage = MagicMock()
        event = {"Records": [
            _s3_record("test-bucket", "ncaafb/games/2025/regular/4.json"),
            _s3_record("test-bucket", "ncaafb/games/2025/regular/5.json"),
        ]}

        with patch.object(ncaafb_normalize, "_s3", mock_s3), \
             patch("ncaafb_normalize.PipelineStorage", return_value=mock_storage), \
             patch.object(ncaafb_normalize, "game_to_event_item", return_value={"pk": "e"}):
            result = ncaafb_normalize.lambda_handler(event, None)

        assert result == {"processed": 1, "failed": 1}

    def test_url_decodes_s3_object_key(self):
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response([])
        event = {"Records": [_s3_record("test-bucket", "ncaafb/games/2025/regular/4%20extra.json")]}

        with patch.object(ncaafb_normalize, "_s3", mock_s3), \
             patch("ncaafb_normalize.PipelineStorage"):
            ncaafb_normalize.lambda_handler(event, None)

        mock_s3.get_object.assert_called_once_with(Bucket="test-bucket", Key="ncaafb/games/2025/regular/4 extra.json")

    def test_returns_empty_result_for_no_records(self):
        with patch.object(ncaafb_normalize, "_s3", MagicMock()), \
             patch("ncaafb_normalize.PipelineStorage"):
            result = ncaafb_normalize.lambda_handler({"Records": []}, None)

        assert result == {"processed": 0, "failed": 0}

    def test_storage_singleton_reused_across_records(self):
        payload = [{"id": "1"}]
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage_cls = MagicMock(return_value=MagicMock())
        event = {"Records": [
            _s3_record("test-bucket", "ncaafb/games/2025/regular/4.json"),
            _s3_record("test-bucket", "ncaafb/games/2025/regular/5.json"),
        ]}

        with patch.object(ncaafb_normalize, "_s3", mock_s3), \
             patch("ncaafb_normalize.PipelineStorage", mock_storage_cls), \
             patch.object(ncaafb_normalize, "game_to_event_item", return_value={"pk": "e"}):
            ncaafb_normalize.lambda_handler(event, None)

        assert mock_storage_cls.call_count == 1
