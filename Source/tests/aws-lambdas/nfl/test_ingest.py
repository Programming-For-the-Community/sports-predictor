"""
Unit tests for the NFL ingest Lambda handler.

All AWS and ESPN calls are mocked -- these tests run without credentials
and verify handler logic: event routing, idempotency, error resilience,
and return-value counts.

The nfl_ingest module is registered in sys.modules by conftest.py, which
also sets RAW_BUCKET_NAME before the module is imported (it's read at
module level by the handler).
"""
import nfl_ingest
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _completed_event(event_id: str) -> dict:
    return {"id": event_id, "status": {"type": {"completed": True}}}


def _incomplete_event(event_id: str) -> dict:
    return {"id": event_id, "status": {"type": {"completed": False}}}


SEASON_YEAR = 2025
SEASON_TYPE = 2


def _scoreboard(events: list, week: int = 5, season_year: int = SEASON_YEAR, season_type: int = SEASON_TYPE) -> dict:
    return {
        "week": {"number": week},
        "season": {"year": season_year, "type": season_type},
        "events": events,
    }


def _make_s3(existing_keys: set | None = None):
    """Return a mock S3 client where head_object raises 404 for unknown keys."""
    mock_s3 = MagicMock()
    existing = existing_keys or set()

    def _head(**kwargs):
        if kwargs.get("Key") in existing:
            return {}
        raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")

    mock_s3.head_object.side_effect = _head
    return mock_s3


def _make_client(scoreboard: dict, summary: dict | None = None):
    mock = MagicMock()
    mock.get_current_scoreboard.return_value = scoreboard
    mock.get_scoreboard.return_value = scoreboard
    mock.get_summary.return_value = summary or {"header": {}, "boxscore": {}}
    return mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestIngestLambdaHandler:
    def test_processes_completed_events(self):
        board = _scoreboard([_completed_event("123")])
        mock_s3 = _make_s3()
        mock_client = _make_client(board)

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client):
            result = nfl_ingest.lambda_handler({}, None)

        assert result["processed"] == 1
        assert result["failed"] == 0
        mock_client.get_summary.assert_called_once_with("123")
        mock_s3.put_object.assert_called()

    def test_skips_incomplete_events(self):
        board = _scoreboard([_completed_event("1"), _incomplete_event("2")])
        mock_s3 = _make_s3()
        mock_client = _make_client(board)

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client):
            result = nfl_ingest.lambda_handler({}, None)

        assert result["processed"] == 1
        assert result["skipped"] == 1
        mock_client.get_summary.assert_called_once_with("1")

    def test_skips_events_already_in_s3(self):
        board = _scoreboard([_completed_event("123")])
        # Pre-mark the box score key as already present
        existing_key = f"nfl/boxscore/{SEASON_YEAR}/123.json"
        mock_s3 = _make_s3(existing_keys={existing_key})
        mock_client = _make_client(board)

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client):
            result = nfl_ingest.lambda_handler({}, None)

        assert result["processed"] == 0
        assert result["skipped"] == 1
        mock_client.get_summary.assert_not_called()

    def test_uses_explicit_week_from_event_payload(self):
        board = _scoreboard([])
        mock_s3 = _make_s3()
        mock_client = _make_client(board)

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client):
            nfl_ingest.lambda_handler({"season": 2024, "season_type": 2, "week": 3}, None)

        mock_client.get_scoreboard.assert_called_once_with(2024, 2, 3)
        mock_client.get_current_scoreboard.assert_not_called()

    def test_auto_detects_week_when_not_in_payload(self):
        board = _scoreboard([], week=7)
        mock_s3 = _make_s3()
        mock_client = _make_client(board)

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client):
            nfl_ingest.lambda_handler({}, None)

        mock_client.get_current_scoreboard.assert_called_once_with(None, None)
        mock_client.get_scoreboard.assert_not_called()

    def test_skips_preseason_given_explicitly(self):
        mock_s3 = _make_s3()
        mock_client = _make_client(_scoreboard([]))

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client):
            result = nfl_ingest.lambda_handler({"season": 2025, "season_type": 1, "week": 1}, None)

        assert result == {"processed": 0, "skipped": 0, "failed": 0}
        mock_client.get_scoreboard.assert_not_called()
        mock_client.get_current_scoreboard.assert_not_called()
        mock_s3.put_object.assert_not_called()

    def test_skips_preseason_when_auto_detected(self):
        board = _scoreboard([_completed_event("1")], season_type=1)
        mock_s3 = _make_s3()
        mock_client = _make_client(board)

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client):
            result = nfl_ingest.lambda_handler({}, None)

        assert result == {"processed": 0, "skipped": 0, "failed": 0}
        mock_client.get_summary.assert_not_called()
        mock_s3.put_object.assert_not_called()

    def test_continues_after_individual_game_failure(self):
        board = _scoreboard([_completed_event("1"), _completed_event("2")])
        mock_s3 = _make_s3()
        mock_client = _make_client(board)
        mock_client.get_summary.side_effect = [Exception("ESPN timeout"), {"header": {}, "boxscore": {}}]

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client):
            result = nfl_ingest.lambda_handler({}, None)

        assert result["processed"] == 1
        assert result["failed"] == 1

    def test_returns_correct_aggregate_counts(self):
        board = _scoreboard([
            _completed_event("1"),  # processed
            _incomplete_event("2"),  # skipped (not done)
            _completed_event("3"),  # skipped (already in S3)
        ])
        existing_key = f"nfl/boxscore/{SEASON_YEAR}/3.json"
        mock_s3 = _make_s3(existing_keys={existing_key})
        mock_client = _make_client(board)

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client):
            result = nfl_ingest.lambda_handler({}, None)

        assert result == {"processed": 1, "skipped": 2, "failed": 0}

    def test_returns_empty_counts_for_empty_scoreboard(self):
        board = _scoreboard([])
        mock_s3 = _make_s3()
        mock_client = _make_client(board)

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client):
            result = nfl_ingest.lambda_handler({}, None)

        assert result == {"processed": 0, "skipped": 0, "failed": 0}
        mock_client.get_summary.assert_not_called()


class TestIngestHelpers:
    def test_object_exists_returns_true_when_key_found(self):
        mock_s3 = MagicMock()
        mock_s3.head_object.return_value = {"ContentLength": 100}

        with patch.object(nfl_ingest, "_s3", mock_s3):
            assert nfl_ingest._object_exists("some/key.json") is True

    def test_object_exists_returns_false_on_client_error(self):
        mock_s3 = MagicMock()
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
        )

        with patch.object(nfl_ingest, "_s3", mock_s3):
            assert nfl_ingest._object_exists("missing/key.json") is False

    def test_put_json_serialises_payload_correctly(self):
        mock_s3 = MagicMock()
        payload = {"events": [{"id": "123"}]}

        with patch.object(nfl_ingest, "_s3", mock_s3):
            nfl_ingest._put_json("nfl/test.json", payload)

        call_kwargs = mock_s3.put_object.call_args.kwargs
        assert call_kwargs["Key"] == "nfl/test.json"
        assert call_kwargs["ContentType"] == "application/json"
        assert '"events"' in call_kwargs["Body"]