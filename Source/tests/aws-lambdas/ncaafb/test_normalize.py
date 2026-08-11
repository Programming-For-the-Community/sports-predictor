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
from datetime import date, timedelta

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
        mock_storage.get_entity.return_value = None  # no prior roster-set entity to preserve
        stats = [{"pk": "stat1"}]
        entities = [{"entity_id": "1", "pk": "ncaafb#player#1", "metadata": {"position": None}}]

        with patch.object(ncaafb_normalize, "_s3", mock_s3), \
             patch("ncaafb_normalize.PipelineStorage", return_value=mock_storage), \
             patch.object(ncaafb_normalize, "game_player_stats_to_player_game_stats", return_value=(stats, entities)):
            ncaafb_normalize._dispatch("test-bucket", "ncaafb/boxscore/2025/regular/4.json")

        assert mock_storage.upsert_player_entity.call_count == 2  # once per game entry
        assert mock_storage.write_player_game_stats.call_count == 2

    def test_routes_roster_key_to_roster_processor(self):
        payload = {"fetched_at": "2026-01-15T00:00:00+00:00", "data": [{"id": "1", "teamId": "2", "position": "QB"}]}
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage = MagicMock()
        entities = [{"entity_id": "1", "metadata": {"position": "QB"}}]

        with patch.object(ncaafb_normalize, "_s3", mock_s3), \
             patch("ncaafb_normalize.PipelineStorage", return_value=mock_storage), \
             patch.object(ncaafb_normalize, "roster_to_player_entities", return_value=entities) as mock_transform:
            ncaafb_normalize._dispatch("test-bucket", "ncaafb/roster/2025.json")

        mock_transform.assert_called_once_with(payload["data"], "ncaafb", "2026-01-15")
        mock_storage.upsert_player_entity.assert_called_once_with(entities[0])

    def test_logs_a_write_rejected_by_the_staleness_guard_separately_from_a_real_write(self, caplog):
        # A rejection here means some other write already landed a same-
        # day-or-newer team_id_as_of for that entity_id first (see
        # PipelineStorage.upsert_player_entity's own docstring) -- the
        # aggregate "wrote N" count alone can't tell a real, silent
        # rejection apart from roster_to_player_entities finding nothing
        # to write in the first place, so the log line needs both numbers.
        payload = {"fetched_at": "2026-01-15T00:00:00+00:00", "data": [{"id": "1"}, {"id": "2"}]}
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage = MagicMock()
        mock_storage.upsert_player_entity.side_effect = [True, False]
        entities = [{"entity_id": "1", "metadata": {}}, {"entity_id": "2", "metadata": {}}]

        with patch.object(ncaafb_normalize, "_s3", mock_s3), \
             patch("ncaafb_normalize.PipelineStorage", return_value=mock_storage), \
             patch.object(ncaafb_normalize, "roster_to_player_entities", return_value=entities), \
             caplog.at_level("INFO"):
            ncaafb_normalize._dispatch("test-bucket", "ncaafb/roster/2025.json")

        assert "Wrote 1 player entities (1 rejected by the staleness guard)" in caplog.text

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


class TestPreserveRosterPosition:
    def test_carries_forward_existing_position_when_box_score_entity_has_none(self):
        mock_storage = MagicMock()
        mock_storage.get_entity.return_value = {"metadata": {"position": "QB"}}
        entity = {"entity_id": "1", "metadata": {"position": None}}

        ncaafb_normalize._preserve_roster_position(mock_storage, entity)

        assert entity["metadata"]["position"] == "QB"

    def test_no_op_when_box_score_entity_already_has_a_position(self):
        # Never actually happens for CFBD box scores today (position is
        # always None), but this stays a no-op if that ever changes rather
        # than overwriting a real value with a stale lookup.
        mock_storage = MagicMock()
        entity = {"entity_id": "1", "metadata": {"position": "RB"}}

        ncaafb_normalize._preserve_roster_position(mock_storage, entity)

        assert entity["metadata"]["position"] == "RB"
        mock_storage.get_entity.assert_not_called()

    def test_no_op_when_no_existing_entity_found(self):
        mock_storage = MagicMock()
        mock_storage.get_entity.return_value = None
        entity = {"entity_id": "1", "metadata": {"position": None}}

        ncaafb_normalize._preserve_roster_position(mock_storage, entity)

        assert entity["metadata"]["position"] is None

    def test_no_op_when_existing_entity_also_has_no_position(self):
        mock_storage = MagicMock()
        mock_storage.get_entity.return_value = {"metadata": {"position": None}}
        entity = {"entity_id": "1", "metadata": {"position": None}}

        ncaafb_normalize._preserve_roster_position(mock_storage, entity)

        assert entity["metadata"]["position"] is None

    def test_lookup_failure_is_swallowed(self):
        mock_storage = MagicMock()
        mock_storage.get_entity.side_effect = Exception("DynamoDB timeout")
        entity = {"entity_id": "1", "metadata": {"position": None}}

        ncaafb_normalize._preserve_roster_position(mock_storage, entity)  # does not raise

        assert entity["metadata"]["position"] is None


class TestCancelStaleScheduledEvents:
    def test_cancels_an_event_over_a_year_past_its_own_date(self):
        storage = MagicMock()
        old_date = (date.today() - timedelta(days=400)).isoformat()
        stale_event = {"event_key": "k1", "sport": "ncaafb", "status": "scheduled", "event_date": old_date}
        storage.get_events_by_status.return_value = [stale_event]

        count = ncaafb_normalize._cancel_stale_scheduled_events(storage)

        assert count == 1
        assert stale_event["status"] == "canceled"
        storage.upsert_event.assert_called_once_with(stale_event)

    def test_leaves_a_recent_scheduled_event_alone(self):
        storage = MagicMock()
        recent_date = (date.today() - timedelta(days=10)).isoformat()
        storage.get_events_by_status.return_value = [
            {"event_key": "k1", "status": "scheduled", "event_date": recent_date},
        ]

        count = ncaafb_normalize._cancel_stale_scheduled_events(storage)

        assert count == 0
        storage.upsert_event.assert_not_called()

    def test_queries_the_scheduled_status(self):
        storage = MagicMock()
        storage.get_events_by_status.return_value = []

        ncaafb_normalize._cancel_stale_scheduled_events(storage)

        storage.get_events_by_status.assert_called_once_with("ncaafb", "scheduled")


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

    def test_runs_stale_event_cleanup_once_regardless_of_record_count(self):
        mock_storage = MagicMock()
        mock_storage.get_events_by_status.return_value = []

        with patch.object(ncaafb_normalize, "_s3", MagicMock()), \
             patch("ncaafb_normalize.PipelineStorage", return_value=mock_storage):
            ncaafb_normalize.lambda_handler({"Records": []}, None)

        mock_storage.get_events_by_status.assert_called_once_with("ncaafb", "scheduled")

    def test_a_stale_event_cleanup_failure_does_not_crash_the_run(self):
        mock_storage = MagicMock()
        mock_storage.get_events_by_status.side_effect = Exception("DynamoDB timeout")

        with patch.object(ncaafb_normalize, "_s3", MagicMock()), \
             patch("ncaafb_normalize.PipelineStorage", return_value=mock_storage):
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
