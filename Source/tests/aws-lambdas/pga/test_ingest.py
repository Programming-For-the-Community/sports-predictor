"""
Unit tests for the PGA ingest Lambda handler: date resolution (explicit
override vs. today auto-detection), the discover-then-fetch-leaderboard
flow, per-event failure isolation, and the daily season-stats snapshot
capture (see handler.py's own docstring for why this runs unconditionally
every day, regardless of whether a tournament is current). All AWS and
ESPN calls are mocked.

The pga_ingest module is registered in sys.modules by conftest.py.
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pga_ingest


def _scoreboard(events):
    return {"events": events}


def _event(event_id="401811963", season_year=2026):
    return {"id": event_id, "season": {"year": season_year}}


def _client(events=None, statistics=None):
    """A mock PGAClient with a real (JSON-serializable) get_statistics
    return value by default -- a bare MagicMock() there would make every
    _fetch_statistics_snapshot call fail (json.dumps can't serialize a
    MagicMock), which is harmless (best-effort, caught) but would make
    every test's stats_captured assertion misleadingly always False."""
    client = MagicMock()
    client.get_scoreboard_for_date.return_value = _scoreboard(events or [])
    client.get_statistics.return_value = statistics if statistics is not None else {"stats": {"categories": []}}
    return client


class TestLambdaHandlerDateResolution:
    def test_uses_explicit_date_override(self):
        mock_client = _client()

        with patch.object(pga_ingest, "PGAClient", return_value=mock_client), \
             patch.object(pga_ingest, "_s3", MagicMock()):
            pga_ingest.lambda_handler({"date": "20260824"}, None)

        mock_client.get_scoreboard_for_date.assert_called_once_with("20260824")

    def test_defaults_to_today_when_no_date_given(self):
        mock_client = _client()

        with patch.object(pga_ingest, "PGAClient", return_value=mock_client), \
             patch.object(pga_ingest, "_s3", MagicMock()), \
             patch("pga_ingest.date") as mock_date:
            mock_date.today.return_value = date(2026, 8, 24)
            pga_ingest.lambda_handler({}, None)

        mock_client.get_scoreboard_for_date.assert_called_once_with("20260824")


class TestLambdaHandlerLeaderboardFetch:
    def test_no_current_events_writes_nothing(self):
        mock_client = _client()
        mock_s3 = MagicMock()

        with patch.object(pga_ingest, "PGAClient", return_value=mock_client), \
             patch.object(pga_ingest, "_s3", mock_s3):
            result = pga_ingest.lambda_handler({"date": "20260824"}, None)

        assert result == {"processed": 0, "failed": 0, "stats_captured": True}
        written_keys = [c.kwargs["Key"] for c in mock_s3.put_object.call_args_list]
        assert written_keys == ["pga/statistics/20260824.json"]  # stats still written, just no tournaments

    def test_current_event_fetches_and_writes_the_leaderboard(self):
        mock_client = _client(events=[_event(event_id="401811963", season_year=2026)])
        mock_client.get_leaderboard.return_value = {"events": [{"id": "401811963"}]}
        mock_s3 = MagicMock()

        with patch.object(pga_ingest, "PGAClient", return_value=mock_client), \
             patch.object(pga_ingest, "_s3", mock_s3):
            result = pga_ingest.lambda_handler({"date": "20260824"}, None)

        assert result == {"processed": 1, "failed": 0, "stats_captured": True}
        mock_client.get_leaderboard.assert_called_once_with("401811963")
        written_keys = [c.kwargs["Key"] for c in mock_s3.put_object.call_args_list]
        assert "pga/leaderboard/2026/401811963.json" in written_keys

    def test_one_events_fetch_failure_does_not_block_the_others(self):
        mock_client = _client(events=[_event(event_id="1"), _event(event_id="2")])
        mock_client.get_leaderboard.side_effect = [Exception("ESPN timeout"), {"events": [{"id": "2"}]}]
        mock_s3 = MagicMock()

        with patch.object(pga_ingest, "PGAClient", return_value=mock_client), \
             patch.object(pga_ingest, "_s3", mock_s3):
            result = pga_ingest.lambda_handler({"date": "20260824"}, None)

        assert result == {"processed": 1, "failed": 1, "stats_captured": True}


class TestLambdaHandlerStatisticsSnapshot:
    def test_captures_a_snapshot_under_the_target_date(self):
        mock_client = _client(statistics={"stats": {"categories": [{"name": "scoringAverage"}]}})
        mock_s3 = MagicMock()

        with patch.object(pga_ingest, "PGAClient", return_value=mock_client), \
             patch.object(pga_ingest, "_s3", mock_s3):
            pga_ingest.lambda_handler({"date": "20260824"}, None)

        mock_client.get_statistics.assert_called_once()
        written_keys = [c.kwargs["Key"] for c in mock_s3.put_object.call_args_list]
        assert "pga/statistics/20260824.json" in written_keys

    def test_runs_even_when_no_tournament_is_current(self):
        # The whole point -- see handler.py's own docstring. No events at
        # all this run, stats should still be captured.
        mock_client = _client(events=[])
        mock_s3 = MagicMock()

        with patch.object(pga_ingest, "PGAClient", return_value=mock_client), \
             patch.object(pga_ingest, "_s3", mock_s3):
            result = pga_ingest.lambda_handler({"date": "20260824"}, None)

        assert result["stats_captured"] is True

    def test_a_failed_stats_fetch_does_not_block_leaderboard_ingest(self):
        mock_client = _client(events=[_event(event_id="401811963")])
        mock_client.get_statistics.side_effect = Exception("ESPN timeout")
        mock_client.get_leaderboard.return_value = {"events": [{"id": "401811963"}]}
        mock_s3 = MagicMock()

        with patch.object(pga_ingest, "PGAClient", return_value=mock_client), \
             patch.object(pga_ingest, "_s3", mock_s3):
            result = pga_ingest.lambda_handler({"date": "20260824"}, None)

        assert result["stats_captured"] is False
        assert result["processed"] == 1  # leaderboard ingest still succeeded
