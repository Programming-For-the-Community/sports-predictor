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
    return {"id": event_id, "date": "2026-08-20T04:00Z", "tournament": {"scoringSystem": {"name": "Medal"}}, "competitions": [{"competitors": competitors}], **extra}


def _match_event(event_id="401465497", tournament_name="Presidents Cup", include_cup_summary=True, match_sessions=None):
    """A team-match-play (Ryder Cup/Presidents Cup) or individual-match-
    play (WGC Match Play, include_cup_summary=False) leaderboard event --
    the NESTED `[[...], [...]]` shape confirmed live 2026-08-26 (see
    library.normalize.pga_matchplay's own module docstring)."""
    sessions = []
    if include_cup_summary:
        sessions.append([{
            "id": "10950", "description": "tournament", "type": {"text": "tournament"},
            "scoringSystem": {"name": "Cup"},
            "competitors": [
                {"id": "1", "homeAway": "home", "score": {"value": 17.5, "winner": True}, "team": {"id": "1", "displayName": "USA"}},
                {"id": "3", "homeAway": "away", "score": {"value": 12.5, "winner": False}, "team": {"id": "3", "displayName": "INTL"}},
            ],
        }])
    if match_sessions is None:
        match_sessions = [[{
            "id": "10951", "date": "2022-09-22T17:05Z", "description": "Thursday Foursomes",
            "type": {"text": "foursome"}, "scoringSystem": {"name": "Match"},
            "status": {"type": {"name": "STATUS_FINAL", "completed": True}},
            "competitors": [
                {
                    "id": "1085", "homeAway": "home", "score": {"value": 6.0, "displayValue": "6 & 5", "winner": True},
                    "team": {"id": "1", "displayName": "USA"},
                    "roster": [{"athlete": {"id": "1085", "displayName": "Tony Finau"}}, {"athlete": {"id": "1086", "displayName": "Max Homa"}}],
                },
                {
                    "id": "2001", "homeAway": "away", "score": {"value": 0.0, "displayValue": "", "winner": False},
                    "team": {"id": "3", "displayName": "INTL"},
                    "roster": [{"athlete": {"id": "2001", "displayName": "Hideki Matsuyama"}}, {"athlete": {"id": "2002", "displayName": "Sungjae Im"}}],
                },
            ],
        }]]
    sessions.extend(match_sessions)
    return {
        "id": event_id,
        "date": "2022-09-22T17:05Z",
        "season": {"year": 2023},
        "tournament": {"displayName": tournament_name, "scoringSystem": {"name": "Match"}},
        "status": {"type": {"name": "STATUS_FINAL", "completed": True}},
        "competitions": sessions,
    }


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

    def test_unrecognized_scoring_system_is_skipped_without_upserting(self):
        payload = {"events": [{"id": "401219595", "tournament": {"displayName": "Barracuda Championship", "scoringSystem": {"name": "Stableford"}}}]}
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage = MagicMock()

        with patch.object(pga_normalize, "_s3", mock_s3), \
             patch("pga_normalize.PipelineStorage", return_value=mock_storage):
            pga_normalize._dispatch("test-bucket", "pga/leaderboard/2026/401219595.json")  # must not raise

        mock_storage.upsert_event.assert_not_called()
        mock_storage.upsert_entity.assert_not_called()

    def test_the_match_exhibition_is_skipped_without_upserting(self):
        # The Match -- team+roster shape identical to Ryder Cup's, but no
        # Cup-level summary entry and no guarantee its "athletes" are
        # even PGA Tour golfers (see library.normalize.pga_matchplay.
        # is_exhibition's own docstring). Excluded permanently.
        payload = {"events": [_match_event("401430881", tournament_name="The Match", include_cup_summary=False)]}
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage = MagicMock()

        with patch.object(pga_normalize, "_s3", mock_s3), \
             patch("pga_normalize.PipelineStorage", return_value=mock_storage):
            pga_normalize._dispatch("test-bucket", "pga/leaderboard/2026/401430881.json")  # must not raise

        mock_storage.upsert_event.assert_not_called()
        mock_storage.upsert_entity.assert_not_called()

    def test_team_match_play_event_is_upserted(self):
        # Ryder Cup / Presidents Cup -- 1 cup event + 1 match event, plus
        # national-team and golfer entities.
        payload = {"events": [_match_event()]}
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage = MagicMock()

        with patch.object(pga_normalize, "_s3", mock_s3), \
             patch("pga_normalize.PipelineStorage", return_value=mock_storage):
            pga_normalize._dispatch("test-bucket", "pga/leaderboard/2023/401465497.json")

        assert mock_storage.upsert_event.call_count == 2
        event_types = {call.args[0]["event_type"] for call in mock_storage.upsert_event.call_args_list}
        assert event_types == {"cup", "match_play"}
        assert mock_storage.upsert_entity.call_count == 6  # 2 teams + 4 golfers

    def test_individual_match_play_event_is_upserted_with_no_cup_event(self):
        # WGC-Dell Technologies Match Play -- no team layer, no Cup
        # summary, so no "cup" event row, only "match_play" ones.
        payload = {"events": [_match_event(
            "401353293", tournament_name="WGC-Dell Technologies Match Play", include_cup_summary=False,
            match_sessions=[[{
                "id": "1", "date": "2022-03-23T07:00Z", "description": "Wednesday Group Play",
                "type": {"text": "singles"}, "scoringSystem": {"name": "Match"},
                "status": {"type": {"name": "STATUS_FINAL", "completed": True}},
                "competitors": [
                    {"id": "3439", "homeAway": "home", "score": {"value": 3.0, "displayValue": "3 & 2", "winner": True}, "athlete": {"id": "3439", "displayName": "Scottie Scheffler"}},
                    {"id": "3448", "homeAway": "away", "score": {"value": 0.0, "displayValue": "", "winner": False}, "athlete": {"id": "3448", "displayName": "Cameron Young"}},
                ],
            }]],
        )]}
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage = MagicMock()

        with patch.object(pga_normalize, "_s3", mock_s3), \
             patch("pga_normalize.PipelineStorage", return_value=mock_storage):
            pga_normalize._dispatch("test-bucket", "pga/leaderboard/2022/401353293.json")

        assert mock_storage.upsert_event.call_count == 1
        assert mock_storage.upsert_event.call_args[0][0]["event_type"] == "match_play"
        assert mock_storage.upsert_entity.call_count == 2  # 2 golfers, no team entities

    def test_match_play_event_with_no_match_data_is_skipped_without_upserting(self):
        payload = {"events": [_match_event(match_sessions=[])]}
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = _s3_response(payload)
        mock_storage = MagicMock()

        with patch.object(pga_normalize, "_s3", mock_s3), \
             patch("pga_normalize.PipelineStorage", return_value=mock_storage):
            pga_normalize._dispatch("test-bucket", "pga/leaderboard/2023/401465497.json")

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
