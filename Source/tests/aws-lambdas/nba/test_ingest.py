"""
Unit tests for the NBA ingest Lambda handler: date resolution (explicit
override vs. yesterday auto-detection), the preseason skip, the
teams/roster refresh, and box-score idempotency/failure isolation. All
AWS and ESPN calls are mocked.

The nba_ingest module is registered in sys.modules by conftest.py, which
also sets RAW_BUCKET_NAME before the module is imported.
"""
from datetime import date
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

import nba_ingest


def _teams_response(team_ids=("1", "2")):
    return {"sports": [{"leagues": [{"teams": [{"team": {"id": tid}} for tid in team_ids]}]}]}


def _event(event_id="401700001", completed=False, season_type=2):
    return {
        "id": event_id,
        "season": {"year": 2026, "type": season_type},
        "status": {"type": {"completed": completed}},
    }


def _scoreboard(events):
    return {"events": events}


class TestLambdaHandlerDateResolution:
    def test_uses_explicit_date_override_without_computing_yesterday(self):
        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=())
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([])

        with patch.object(nba_ingest, "NBAClient", return_value=mock_client), \
             patch.object(nba_ingest, "_s3", MagicMock()):
            nba_ingest.lambda_handler({"date": "20260101"}, None)

        mock_client.get_scoreboard_for_date.assert_called_once_with("20260101")

    def test_defaults_to_yesterday_when_no_date_given(self):
        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=())
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([])

        with patch.object(nba_ingest, "NBAClient", return_value=mock_client), \
             patch.object(nba_ingest, "_s3", MagicMock()), \
             patch.object(nba_ingest, "_yesterday", return_value="20260113"):
            nba_ingest.lambda_handler({}, None)

        mock_client.get_scoreboard_for_date.assert_called_once_with("20260113")


class TestLambdaHandlerPreseason:
    def test_preseason_date_is_skipped_and_not_written(self):
        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=())
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([_event(season_type=1)])
        mock_s3 = MagicMock()

        with patch.object(nba_ingest, "NBAClient", return_value=mock_client), \
             patch.object(nba_ingest, "_s3", mock_s3):
            result = nba_ingest.lambda_handler({"date": "20251008"}, None)

        assert result == {
            "processed": 0, "skipped": 0, "failed": 0,
            "rosters_fetched": 0, "rosters_failed": 0,
        }
        written_keys = [c.kwargs["Key"] for c in mock_s3.put_object.call_args_list]
        assert not any("/scoreboard/" in k for k in written_keys)

    def test_regular_season_date_is_processed_normally(self):
        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=())
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([_event(season_type=2, completed=False)])
        mock_s3 = MagicMock()

        with patch.object(nba_ingest, "NBAClient", return_value=mock_client), \
             patch.object(nba_ingest, "_s3", mock_s3):
            result = nba_ingest.lambda_handler({"date": "20260114"}, None)

        assert result["skipped"] == 1  # incomplete event, no box score fetched
        written_keys = [c.kwargs["Key"] for c in mock_s3.put_object.call_args_list]
        assert "nba/scoreboard/20260114.json" in written_keys

    def test_empty_scoreboard_is_not_treated_as_preseason(self):
        # No events at all (an off night) shouldn't hit events[0] and
        # crash, and shouldn't be misread as a preseason skip either.
        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=())
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([])
        mock_s3 = MagicMock()

        with patch.object(nba_ingest, "NBAClient", return_value=mock_client), \
             patch.object(nba_ingest, "_s3", mock_s3):
            result = nba_ingest.lambda_handler({"date": "20260114"}, None)

        assert result["processed"] == 0
        assert result["skipped"] == 0
        written_keys = [c.kwargs["Key"] for c in mock_s3.put_object.call_args_list]
        assert "nba/scoreboard/20260114.json" in written_keys


class TestLambdaHandlerTeamsAndRosters:
    def test_teams_response_is_written_once_and_reused_for_roster_ids(self):
        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=("1", "2"))
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([])
        mock_client.get_roster.return_value = {"team": {"id": "1"}, "timestamp": "2026-01-01T00:00Z", "athletes": []}
        mock_s3 = MagicMock()

        with patch.object(nba_ingest, "NBAClient", return_value=mock_client), \
             patch.object(nba_ingest, "_s3", mock_s3):
            result = nba_ingest.lambda_handler({"date": "20260114"}, None)

        mock_client.get_teams.assert_called_once()
        assert result["rosters_fetched"] == 2
        assert mock_client.get_roster.call_count == 2
        written_keys = [c.kwargs["Key"] for c in mock_s3.put_object.call_args_list]
        assert "nba/teams.json" in written_keys
        assert "nba/roster/1.json" in written_keys
        assert "nba/roster/2.json" in written_keys

    def test_one_teams_roster_failure_does_not_block_the_others(self):
        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=("1", "2"))
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([])
        mock_client.get_roster.side_effect = [Exception("ESPN timeout"), {"team": {"id": "2"}, "timestamp": "2026-01-01T00:00Z", "athletes": []}]

        with patch.object(nba_ingest, "NBAClient", return_value=mock_client), \
             patch.object(nba_ingest, "_s3", MagicMock()):
            result = nba_ingest.lambda_handler({"date": "20260114"}, None)

        assert result["rosters_fetched"] == 1
        assert result["rosters_failed"] == 1


class TestLambdaHandlerBoxScores:
    def test_incomplete_event_is_skipped(self):
        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=())
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([_event(completed=False)])

        with patch.object(nba_ingest, "NBAClient", return_value=mock_client), \
             patch.object(nba_ingest, "_s3", MagicMock()):
            result = nba_ingest.lambda_handler({"date": "20260114"}, None)

        assert result["skipped"] == 1
        mock_client.get_summary.assert_not_called()

    def test_completed_event_fetches_and_writes_box_score(self):
        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=())
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([_event(event_id="1", completed=True)])
        mock_client.get_summary.return_value = {"header": {"id": "1"}}
        mock_s3 = MagicMock()
        mock_s3.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")

        with patch.object(nba_ingest, "NBAClient", return_value=mock_client), \
             patch.object(nba_ingest, "_s3", mock_s3):
            result = nba_ingest.lambda_handler({"date": "20260114"}, None)

        assert result["processed"] == 1
        mock_client.get_summary.assert_called_once_with("1")
        written_keys = [c.kwargs["Key"] for c in mock_s3.put_object.call_args_list]
        assert "nba/boxscore/2026/1.json" in written_keys

    def test_already_fetched_box_score_is_skipped_not_refetched(self):
        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=())
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([_event(event_id="1", completed=True)])
        mock_s3 = MagicMock()
        mock_s3.head_object.return_value = {}  # object exists -- no ClientError raised

        with patch.object(nba_ingest, "NBAClient", return_value=mock_client), \
             patch.object(nba_ingest, "_s3", mock_s3):
            result = nba_ingest.lambda_handler({"date": "20260114"}, None)

        assert result["skipped"] == 1
        assert result["processed"] == 0
        mock_client.get_summary.assert_not_called()

    def test_one_event_summary_failure_does_not_block_others(self):
        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=())
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([
            _event(event_id="1", completed=True), _event(event_id="2", completed=True),
        ])
        mock_client.get_summary.side_effect = [Exception("ESPN timeout"), {"header": {"id": "2"}}]
        mock_s3 = MagicMock()
        mock_s3.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")

        with patch.object(nba_ingest, "NBAClient", return_value=mock_client), \
             patch.object(nba_ingest, "_s3", mock_s3):
            result = nba_ingest.lambda_handler({"date": "20260114"}, None)

        assert result["processed"] == 1
        assert result["failed"] == 1


class TestYesterday:
    def test_returns_the_day_before_today(self):
        assert nba_ingest._yesterday(date(2026, 1, 15)) == "20260114"

    def test_crosses_a_month_boundary(self):
        assert nba_ingest._yesterday(date(2026, 3, 1)) == "20260228"
