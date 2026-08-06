"""
Unit tests for the NFL normalize Lambda handler.

All AWS and DynamoDB calls are mocked. Tests verify key routing via _dispatch,
that each processor calls the right storage methods the right number of times,
and that the handler is resilient to individual record failures.

The nfl_normalize module is registered in sys.modules by conftest.py.
"""
import json

import pytest

import nfl_normalize
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_storage():
    """Clear the _storage singleton before and after each test so that
    PipelineStorage mocks are created fresh and don't leak between tests."""
    nfl_normalize._storage = None
    yield
    nfl_normalize._storage = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _s3_response(payload: dict) -> dict:
    body = MagicMock()
    body.read.return_value = json.dumps(payload).encode()
    return {"Body": body}


def _s3_record(bucket: str, key: str) -> dict:
    return {"s3": {"bucket": {"name": bucket}, "object": {"key": key}}}


# ---------------------------------------------------------------------------
# Dispatch routing tests
# ---------------------------------------------------------------------------

class TestDispatch:
    def test_routes_teams_json_to_teams_processor(self):
        payload = {"sports": [{"leagues": [{"teams": [
            {"team": {"id": "1"}},
            {"team": {"id": "2"}},
        ]}]}]}
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage = MagicMock()
        entity_stub = {"pk": "nfl#team#1"}

        with patch.object(nfl_normalize, "_s3", mock_s3), \
             patch("nfl_normalize.PipelineStorage", return_value=mock_storage), \
             patch.object(nfl_normalize, "team_to_entity", return_value=entity_stub):
            nfl_normalize._dispatch("test-bucket", "nfl/teams.json")

        assert mock_storage.upsert_entity.call_count == 2

    def test_routes_scoreboard_key_to_scoreboard_processor(self):
        payload = {"events": [{"id": "1"}, {"id": "2"}, {"id": "3"}]}
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage = MagicMock()
        event_stub = {"pk": "nfl#event#1"}

        with patch.object(nfl_normalize, "_s3", mock_s3), \
             patch("nfl_normalize.PipelineStorage", return_value=mock_storage), \
             patch.object(nfl_normalize, "scoreboard_event_to_event_item", return_value=event_stub):
            nfl_normalize._dispatch("test-bucket", "nfl/scoreboard/2025/2/5.json")

        assert mock_storage.upsert_event.call_count == 3

    def test_routes_boxscore_key_to_boxscore_processor(self):
        payload = {"header": {}, "boxscore": {}}
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage = MagicMock()
        stats = [{"pk": "stat1"}, {"pk": "stat2"}]
        entities = [{"pk": "nfl#player#999"}]
        team_stats = [{"pk": "team-stat1"}]

        with patch.object(nfl_normalize, "_s3", mock_s3), \
             patch("nfl_normalize.PipelineStorage", return_value=mock_storage), \
             patch.object(nfl_normalize, "boxscore_to_player_game_stats", return_value=(stats, entities)), \
             patch.object(nfl_normalize, "boxscore_to_team_game_stats", return_value=team_stats):
            nfl_normalize._dispatch("test-bucket", "nfl/boxscore/2025/401547603.json")

        mock_storage.upsert_player_entity.assert_called_once_with(entities[0])
        mock_storage.write_player_game_stats.assert_called_once_with(stats)
        mock_storage.write_team_game_stats.assert_called_once_with(team_stats)

    def test_ignores_unrecognized_key_without_raising(self):
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response({"unexpected": "shape"})
        mock_storage = MagicMock()

        with patch.object(nfl_normalize, "_s3", mock_s3), \
             patch("nfl_normalize.PipelineStorage", return_value=mock_storage):
            nfl_normalize._dispatch("test-bucket", "nfl/random/unknown.json")

        mock_storage.upsert_entity.assert_not_called()
        mock_storage.upsert_player_entity.assert_not_called()
        mock_storage.upsert_event.assert_not_called()
        mock_storage.write_player_game_stats.assert_not_called()
        mock_storage.write_team_game_stats.assert_not_called()

    def test_scoreboard_key_with_no_events_does_not_call_upsert(self):
        payload = {"events": []}
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage = MagicMock()

        with patch.object(nfl_normalize, "_s3", mock_s3), \
             patch("nfl_normalize.PipelineStorage", return_value=mock_storage), \
             patch.object(nfl_normalize, "scoreboard_event_to_event_item"):
            nfl_normalize._dispatch("test-bucket", "nfl/scoreboard/2025/2/5.json")

        mock_storage.upsert_event.assert_not_called()

    def test_s3_get_object_uses_correct_bucket_and_key(self):
        payload = {"events": []}
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)

        with patch.object(nfl_normalize, "_s3", mock_s3), \
             patch("nfl_normalize.PipelineStorage"):
            nfl_normalize._dispatch("my-bucket", "nfl/scoreboard/2025/2/5.json")

        mock_s3.get_object.assert_called_once_with(
            Bucket="my-bucket", Key="nfl/scoreboard/2025/2/5.json"
        )


# ---------------------------------------------------------------------------
# Lambda handler tests
# ---------------------------------------------------------------------------

class TestNormalizeLambdaHandler:
    def test_processes_multiple_records(self):
        payload = {"events": [{"id": "1"}]}
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage = MagicMock()
        event = {
            "Records": [
                _s3_record("test-bucket", "nfl/scoreboard/2025/2/5.json"),
                _s3_record("test-bucket", "nfl/scoreboard/2025/2/6.json"),
            ]
        }

        with patch.object(nfl_normalize, "_s3", mock_s3), \
             patch("nfl_normalize.PipelineStorage", return_value=mock_storage), \
             patch.object(nfl_normalize, "scoreboard_event_to_event_item", return_value={"pk": "e"}):
            result = nfl_normalize.lambda_handler(event, None)

        assert result == {"processed": 2, "failed": 0}
        assert mock_s3.get_object.call_count == 2

    def test_continues_after_individual_record_failure(self):
        payload = {"events": [{"id": "1"}]}
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = [
            Exception("S3 timeout"),
            _s3_response(payload),
        ]
        mock_storage = MagicMock()
        event = {
            "Records": [
                _s3_record("test-bucket", "nfl/scoreboard/2025/2/5.json"),
                _s3_record("test-bucket", "nfl/scoreboard/2025/2/6.json"),
            ]
        }

        with patch.object(nfl_normalize, "_s3", mock_s3), \
             patch("nfl_normalize.PipelineStorage", return_value=mock_storage), \
             patch.object(nfl_normalize, "scoreboard_event_to_event_item", return_value={"pk": "e"}):
            result = nfl_normalize.lambda_handler(event, None)

        assert result == {"processed": 1, "failed": 1}

    def test_url_decodes_s3_object_key(self):
        """S3 event notifications percent-encode special characters in keys."""
        payload = {"events": []}
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage = MagicMock()
        # Key contains a URL-encoded space (%20)
        event = {"Records": [_s3_record("test-bucket", "nfl/scoreboard/2025/2/5%20extra.json")]}

        with patch.object(nfl_normalize, "_s3", mock_s3), \
             patch("nfl_normalize.PipelineStorage", return_value=mock_storage):
            nfl_normalize.lambda_handler(event, None)

        mock_s3.get_object.assert_called_once_with(
            Bucket="test-bucket", Key="nfl/scoreboard/2025/2/5 extra.json"
        )

    def test_returns_empty_result_for_no_records(self):
        with patch.object(nfl_normalize, "_s3", MagicMock()), \
             patch("nfl_normalize.PipelineStorage"):
            result = nfl_normalize.lambda_handler({"Records": []}, None)

        assert result == {"processed": 0, "failed": 0}

    def test_storage_singleton_reused_across_records(self):
        """_get_storage() should only instantiate PipelineStorage once per invocation."""
        payload = {"events": [{"id": "1"}]}
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage_cls = MagicMock(return_value=MagicMock())
        event = {
            "Records": [
                _s3_record("test-bucket", "nfl/scoreboard/2025/2/5.json"),
                _s3_record("test-bucket", "nfl/scoreboard/2025/2/6.json"),
            ]
        }

        with patch.object(nfl_normalize, "_s3", mock_s3), \
             patch("nfl_normalize.PipelineStorage", mock_storage_cls), \
             patch.object(nfl_normalize, "scoreboard_event_to_event_item", return_value={"pk": "e"}):
            nfl_normalize.lambda_handler(event, None)

        assert mock_storage_cls.call_count == 1