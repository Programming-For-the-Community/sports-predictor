"""
Unit tests for the PGA normalize Lambda handler. All AWS calls are mocked.
Tests verify key routing via _dispatch (only one raw payload shape exists
for PGA -- see handler.py's own docstring for why), that entities are
upserted before the event that references them, and that the handler is
resilient to individual record failures. The normalizer logic itself
(leaderboard_event_to_event_item / leaderboard_event_to_player_entities)
is covered by tests/library/normalize/test_pga.py -- these tests exercise
the dispatch/wiring only.

The pga_normalize module is registered in sys.modules by conftest.py.
"""
import json

import pytest
from unittest.mock import MagicMock, patch

import pga_normalize


@pytest.fixture(autouse=True)
def reset_storage():
    pga_normalize._storage = None
    yield
    pga_normalize._storage = None


def _s3_response(payload) -> dict:
    body = MagicMock()
    body.read.return_value = json.dumps(payload).encode()
    return {"Body": body}


def _s3_record(bucket: str, key: str) -> dict:
    return {"s3": {"bucket": {"name": bucket}, "object": {"key": key}}}


def _medal_event(event_id="401811963", competitors=None, **extra):
    if competitors is None:
        competitors = [{"athlete": {"id": "1"}}]
    return {"id": event_id, "tournament": {"scoringSystem": {"name": "Medal"}}, "competitions": [{"competitors": competitors}], **extra}


class TestDispatch:
    def test_routes_leaderboard_key_to_the_leaderboard_processor(self):
        payload = {"events": [_medal_event()]}
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage = MagicMock()
        event_stub = {"event_key": "stub"}

        with patch.object(pga_normalize, "_s3", mock_s3), \
             patch("pga_normalize.PipelineStorage", return_value=mock_storage), \
             patch.object(pga_normalize, "leaderboard_event_to_event_item", return_value=event_stub), \
             patch.object(pga_normalize, "leaderboard_event_to_player_entities", return_value=[{"entity_id": "1"}, {"entity_id": "2"}]):
            pga_normalize._dispatch("test-bucket", "pga/leaderboard/2026/401811963.json")

        assert mock_storage.upsert_entity.call_count == 2
        mock_storage.upsert_event.assert_called_once_with(event_stub)

    def test_entities_are_upserted_before_the_event(self):
        payload = {"events": [_medal_event()]}
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage = MagicMock()
        call_order = []
        mock_storage.upsert_entity.side_effect = lambda *a: call_order.append("entity")
        mock_storage.upsert_event.side_effect = lambda *a: call_order.append("event")

        with patch.object(pga_normalize, "_s3", mock_s3), \
             patch("pga_normalize.PipelineStorage", return_value=mock_storage), \
             patch.object(pga_normalize, "leaderboard_event_to_event_item", return_value={}), \
             patch.object(pga_normalize, "leaderboard_event_to_player_entities", return_value=[{"entity_id": "1"}]):
            pga_normalize._dispatch("test-bucket", "pga/leaderboard/2026/401811963.json")

        assert call_order == ["entity", "event"]

    def test_non_medal_scoring_event_is_skipped_without_upserting(self):
        # Ryder Cup/Presidents Cup/WGC Match Play/Zurich Classic -- see
        # library.normalize.pga.is_medal_scoring's own docstring for the
        # confirmed-live crash this guards against.
        payload = {"events": [{"id": "401219595", "tournament": {"displayName": "Ryder Cup", "scoringSystem": {"name": "Match"}}}]}
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage = MagicMock()

        with patch.object(pga_normalize, "_s3", mock_s3), \
             patch("pga_normalize.PipelineStorage", return_value=mock_storage):
            pga_normalize._dispatch("test-bucket", "pga/leaderboard/2026/401219595.json")  # must not raise

        mock_storage.upsert_event.assert_not_called()
        mock_storage.upsert_entity.assert_not_called()

    def test_medal_event_with_no_competitor_data_is_skipped_without_upserting(self):
        # Real, confirmed ESPN gap (see design/DATA_SCHEMA.md and
        # data-backfills/pga/backfill.py's matching check) -- a
        # Medal-scoring event whose competition object has no
        # "competitors" key at all. Must not be written (would corrupt
        # the cutline dataset's field_size feature to 0).
        payload = {"events": [_medal_event(competitors=[], status={"type": {"name": "STATUS_FINAL"}})]}
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage = MagicMock()

        with patch.object(pga_normalize, "_s3", mock_s3), \
             patch("pga_normalize.PipelineStorage", return_value=mock_storage):
            pga_normalize._dispatch("test-bucket", "pga/leaderboard/2026/401811963.json")  # must not raise

        mock_storage.upsert_event.assert_not_called()
        mock_storage.upsert_entity.assert_not_called()

    def test_no_events_in_payload_is_skipped_without_raising(self):
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response({"events": []})
        mock_storage = MagicMock()

        with patch.object(pga_normalize, "_s3", mock_s3), \
             patch("pga_normalize.PipelineStorage", return_value=mock_storage):
            pga_normalize._dispatch("test-bucket", "pga/leaderboard/2026/401811963.json")  # must not raise

        mock_storage.upsert_event.assert_not_called()

    def test_unrecognized_key_is_skipped_without_raising(self):
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response({})

        with patch.object(pga_normalize, "_s3", mock_s3), \
             patch("pga_normalize.PipelineStorage"):
            pga_normalize._dispatch("test-bucket", "pga/unknown/thing.json")  # must not raise


class TestLambdaHandler:
    def test_processes_every_record_independently(self):
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response({"events": []})
        event = {"Records": [
            _s3_record("test-bucket", "pga/leaderboard/2026/1.json"),
            _s3_record("test-bucket", "pga/leaderboard/2026/2.json"),
        ]}

        with patch.object(pga_normalize, "_s3", mock_s3), \
             patch("pga_normalize.PipelineStorage"):
            result = pga_normalize.lambda_handler(event, None)

        assert result == {"processed": 2, "failed": 0}

    def test_one_records_failure_does_not_block_the_others(self):
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = [Exception("S3 error"), _s3_response({"events": []})]
        event = {"Records": [
            _s3_record("test-bucket", "pga/leaderboard/2026/1.json"),
            _s3_record("test-bucket", "pga/leaderboard/2026/2.json"),
        ]}

        with patch.object(pga_normalize, "_s3", mock_s3), \
             patch("pga_normalize.PipelineStorage"):
            result = pga_normalize.lambda_handler(event, None)

        assert result == {"processed": 1, "failed": 1}
