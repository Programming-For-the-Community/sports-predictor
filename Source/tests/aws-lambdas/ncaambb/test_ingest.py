"""
Unit tests for the NCAA MBB ingest Lambda handler: date resolution
(explicit override vs. yesterday auto-detection), the teams/roster
refresh, box-score idempotency/failure isolation, and injury attachment
onto scoreboard events. All AWS and ESPN calls are mocked.

Unlike NBA's own test_ingest.py, there is no preseason-skip test class
here -- confirmed live, 2026-08-19 (see ingest/handler.py's own
docstring), NCAA MBB's ESPN scoreboard has no preseason concept to skip.

Roster/box-score mocks use an argument-aware side_effect (a dict lookup
or small function) rather than a plain positional list -- both fetch
loops run on a ThreadPoolExecutor (see handler.py's own VOLUME docstring
section), so submission order is not guaranteed to match completion
order the way a plain sequential loop's would be.

The ncaambb_ingest module is registered in sys.modules by conftest.py,
which also sets RAW_BUCKET_NAME before the module is imported.
"""
import json
from datetime import date
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

import ncaambb_ingest


def _teams_response(team_ids=("1", "2")):
    return {"sports": [{"leagues": [{"teams": [{"team": {"id": tid}} for tid in team_ids]}]}]}


def _event(event_id="401700001", completed=False, season_type=2, home_id=None, away_id=None):
    evt = {
        "id": event_id,
        "season": {"year": 2026, "type": season_type},
        "status": {"type": {"completed": completed}},
    }
    if home_id is not None or away_id is not None:
        evt["competitions"] = [{"competitors": [
            {"team": {"id": home_id}, "homeAway": "home"},
            {"team": {"id": away_id}, "homeAway": "away"},
        ]}]
    return evt


def _roster(team_id, injuries=None):
    athlete = {"id": f"a{team_id}", "displayName": "Player"}
    if injuries is not None:
        athlete["injuries"] = injuries
    return {"team": {"id": team_id}, "timestamp": "2026-01-01T00:00Z", "athletes": [athlete]}


def _scoreboard(events):
    return {"events": events}


class TestLambdaHandlerDateResolution:
    def test_uses_explicit_date_override_without_computing_yesterday(self):
        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=())
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([])

        with patch.object(ncaambb_ingest, "NCAAMBBClient", return_value=mock_client), \
             patch.object(ncaambb_ingest, "_s3", MagicMock()):
            ncaambb_ingest.lambda_handler({"date": "20260101"}, None)

        mock_client.get_scoreboard_for_date.assert_called_once_with("20260101")

    def test_defaults_to_yesterday_when_no_date_given(self):
        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=())
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([])

        with patch.object(ncaambb_ingest, "NCAAMBBClient", return_value=mock_client), \
             patch.object(ncaambb_ingest, "_s3", MagicMock()), \
             patch.object(ncaambb_ingest, "_yesterday", return_value="20260113"):
            ncaambb_ingest.lambda_handler({}, None)

        mock_client.get_scoreboard_for_date.assert_called_once_with("20260113")


class TestLambdaHandlerScoreboardWrite:
    def test_regular_season_date_is_processed_normally(self):
        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=())
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([_event(season_type=2, completed=False)])
        mock_s3 = MagicMock()

        with patch.object(ncaambb_ingest, "NCAAMBBClient", return_value=mock_client), \
             patch.object(ncaambb_ingest, "_s3", mock_s3):
            result = ncaambb_ingest.lambda_handler({"date": "20260114"}, None)

        assert result["skipped"] == 1  # incomplete event, no box score fetched
        written_keys = [c.kwargs["Key"] for c in mock_s3.put_object.call_args_list]
        assert "ncaambb/scoreboard/20260114.json" in written_keys

    def test_empty_scoreboard_is_written_without_crashing(self):
        # No events at all (an off night) shouldn't hit events[0] and crash.
        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=())
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([])
        mock_s3 = MagicMock()

        with patch.object(ncaambb_ingest, "NCAAMBBClient", return_value=mock_client), \
             patch.object(ncaambb_ingest, "_s3", mock_s3):
            result = ncaambb_ingest.lambda_handler({"date": "20260114"}, None)

        assert result["processed"] == 0
        assert result["skipped"] == 0
        written_keys = [c.kwargs["Key"] for c in mock_s3.put_object.call_args_list]
        assert "ncaambb/scoreboard/20260114.json" in written_keys


class TestLambdaHandlerTeamsAndRosters:
    def test_teams_response_is_written_once_and_reused_for_roster_ids(self):
        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=("1", "2"))
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([])
        mock_client.get_roster.side_effect = lambda team_id: _roster(team_id)
        mock_s3 = MagicMock()

        with patch.object(ncaambb_ingest, "NCAAMBBClient", return_value=mock_client), \
             patch.object(ncaambb_ingest, "_s3", mock_s3):
            result = ncaambb_ingest.lambda_handler({"date": "20260114"}, None)

        mock_client.get_teams.assert_called_once()
        assert result["rosters_fetched"] == 2
        assert mock_client.get_roster.call_count == 2
        written_keys = [c.kwargs["Key"] for c in mock_s3.put_object.call_args_list]
        assert "ncaambb/teams.json" in written_keys
        assert "ncaambb/roster/1.json" in written_keys
        assert "ncaambb/roster/2.json" in written_keys

    def test_a_larger_team_batch_all_fetch_successfully_via_the_thread_pool(self):
        # D1-scale volume check -- confirms the ThreadPoolExecutor fetch
        # path (handler.py's own VOLUME docstring section) handles a
        # batch well beyond NBA's own ~30-team scale without dropping or
        # duplicating any team.
        team_ids = tuple(str(i) for i in range(1, 51))
        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=team_ids)
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([])
        mock_client.get_roster.side_effect = lambda team_id: _roster(team_id)
        mock_s3 = MagicMock()

        with patch.object(ncaambb_ingest, "NCAAMBBClient", return_value=mock_client), \
             patch.object(ncaambb_ingest, "_s3", mock_s3):
            result = ncaambb_ingest.lambda_handler({"date": "20260114"}, None)

        assert result["rosters_fetched"] == 50
        assert result["rosters_failed"] == 0
        written_keys = {c.kwargs["Key"] for c in mock_s3.put_object.call_args_list}
        assert all(f"ncaambb/roster/{tid}.json" in written_keys for tid in team_ids)

    def test_one_teams_roster_failure_does_not_block_the_others(self):
        def _get_roster(team_id):
            if team_id == "1":
                raise Exception("ESPN timeout")
            return _roster(team_id)

        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=("1", "2"))
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([])
        mock_client.get_roster.side_effect = _get_roster

        with patch.object(ncaambb_ingest, "NCAAMBBClient", return_value=mock_client), \
             patch.object(ncaambb_ingest, "_s3", MagicMock()):
            result = ncaambb_ingest.lambda_handler({"date": "20260114"}, None)

        assert result["rosters_fetched"] == 1
        assert result["rosters_failed"] == 1


class TestLambdaHandlerInjuries:
    def test_attaches_home_and_away_injuries_from_same_run_roster_fetch(self):
        rosters = {
            "1": _roster("1", injuries=[{"status": "Out"}]),
            "2": _roster("2", injuries=[{"status": "Questionable"}]),
        }
        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=("1", "2"))
        mock_client.get_roster.side_effect = lambda team_id: rosters[team_id]
        mock_client.get_scoreboard_for_date.return_value = _scoreboard(
            [_event(event_id="E1", completed=False, home_id="1", away_id="2")]
        )
        mock_s3 = MagicMock()

        with patch.object(ncaambb_ingest, "NCAAMBBClient", return_value=mock_client), \
             patch.object(ncaambb_ingest, "_s3", mock_s3):
            ncaambb_ingest.lambda_handler({"date": "20260114"}, None)

        scoreboard_calls = [c for c in mock_s3.put_object.call_args_list if c.kwargs["Key"] == "ncaambb/scoreboard/20260114.json"]
        written = json.loads(scoreboard_calls[0].kwargs["Body"])
        evt = written["events"][0]
        assert evt["home_injuries"] == [{"entity_id": "a1", "status": "Out"}]
        assert evt["away_injuries"] == [{"entity_id": "a2", "status": "Questionable"}]

    def test_a_failed_team_roster_fetch_leaves_that_side_unset_not_empty(self):
        def _get_roster(team_id):
            if team_id == "1":
                raise Exception("ESPN timeout")
            return _roster("2", injuries=[{"status": "Doubtful"}])

        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=("1", "2"))
        mock_client.get_roster.side_effect = _get_roster
        mock_client.get_scoreboard_for_date.return_value = _scoreboard(
            [_event(event_id="E1", completed=False, home_id="1", away_id="2")]
        )
        mock_s3 = MagicMock()

        with patch.object(ncaambb_ingest, "NCAAMBBClient", return_value=mock_client), \
             patch.object(ncaambb_ingest, "_s3", mock_s3):
            ncaambb_ingest.lambda_handler({"date": "20260114"}, None)

        scoreboard_calls = [c for c in mock_s3.put_object.call_args_list if c.kwargs["Key"] == "ncaambb/scoreboard/20260114.json"]
        evt = json.loads(scoreboard_calls[0].kwargs["Body"])["events"][0]
        assert "home_injuries" not in evt
        assert evt["away_injuries"] == [{"entity_id": "a2", "status": "Doubtful"}]


class TestLambdaHandlerBoxScores:
    def test_incomplete_event_is_skipped(self):
        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=())
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([_event(completed=False)])

        with patch.object(ncaambb_ingest, "NCAAMBBClient", return_value=mock_client), \
             patch.object(ncaambb_ingest, "_s3", MagicMock()):
            result = ncaambb_ingest.lambda_handler({"date": "20260114"}, None)

        assert result["skipped"] == 1
        mock_client.get_summary.assert_not_called()

    def test_completed_event_fetches_and_writes_box_score(self):
        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=())
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([_event(event_id="1", completed=True)])
        mock_client.get_summary.side_effect = lambda event_id: {"header": {"id": event_id}}
        mock_s3 = MagicMock()
        mock_s3.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")

        with patch.object(ncaambb_ingest, "NCAAMBBClient", return_value=mock_client), \
             patch.object(ncaambb_ingest, "_s3", mock_s3):
            result = ncaambb_ingest.lambda_handler({"date": "20260114"}, None)

        assert result["processed"] == 1
        mock_client.get_summary.assert_called_once_with("1")
        written_keys = [c.kwargs["Key"] for c in mock_s3.put_object.call_args_list]
        assert "ncaambb/boxscore/2026/1.json" in written_keys

    def test_already_fetched_box_score_is_skipped_not_refetched(self):
        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=())
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([_event(event_id="1", completed=True)])
        mock_s3 = MagicMock()
        mock_s3.head_object.return_value = {}  # object exists -- no ClientError raised

        with patch.object(ncaambb_ingest, "NCAAMBBClient", return_value=mock_client), \
             patch.object(ncaambb_ingest, "_s3", mock_s3):
            result = ncaambb_ingest.lambda_handler({"date": "20260114"}, None)

        assert result["skipped"] == 1
        assert result["processed"] == 0
        mock_client.get_summary.assert_not_called()

    def test_one_event_summary_failure_does_not_block_others(self):
        def _get_summary(event_id):
            if event_id == "1":
                raise Exception("ESPN timeout")
            return {"header": {"id": event_id}}

        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=())
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([
            _event(event_id="1", completed=True), _event(event_id="2", completed=True),
        ])
        mock_client.get_summary.side_effect = _get_summary
        mock_s3 = MagicMock()
        mock_s3.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")

        with patch.object(ncaambb_ingest, "NCAAMBBClient", return_value=mock_client), \
             patch.object(ncaambb_ingest, "_s3", mock_s3):
            result = ncaambb_ingest.lambda_handler({"date": "20260114"}, None)

        assert result["processed"] == 1
        assert result["failed"] == 1

    def test_a_full_night_of_events_all_fetch_successfully_via_the_thread_pool(self):
        # Volume check at roughly the confirmed real-world Saturday scale
        # (~150 games, see handler.py's own VOLUME docstring section).
        events = [_event(event_id=str(i), completed=True) for i in range(150)]
        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=())
        mock_client.get_scoreboard_for_date.return_value = _scoreboard(events)
        mock_client.get_summary.side_effect = lambda event_id: {"header": {"id": event_id}}
        mock_s3 = MagicMock()
        mock_s3.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")

        with patch.object(ncaambb_ingest, "NCAAMBBClient", return_value=mock_client), \
             patch.object(ncaambb_ingest, "_s3", mock_s3):
            result = ncaambb_ingest.lambda_handler({"date": "20260114"}, None)

        assert result["processed"] == 150
        assert result["failed"] == 0
        written_keys = {c.kwargs["Key"] for c in mock_s3.put_object.call_args_list}
        assert all(f"ncaambb/boxscore/2026/{i}.json" in written_keys for i in range(150))


class TestFetchCurrentApPoll:
    def _pointer_response(self, ref="http://sports.core.api.espn.pvt/.../seasons/2026/types/2/weeks/12/rankings/1?lang=en"):
        return {"rankings": [{"id": "1", "type": "ap", "$ref": ref}, {"id": "2", "type": "usa", "$ref": "..."}]}

    def test_fetches_and_writes_the_current_poll(self):
        client = MagicMock()
        client.get_current_rankings_pointer.return_value = self._pointer_response()
        core_client = MagicMock()
        core_client.get_ap_poll.return_value = {"ranks": []}

        with patch.object(ncaambb_ingest, "_s3", MagicMock()):
            result = ncaambb_ingest._fetch_current_ap_poll(client, core_client)

        assert result is True
        core_client.get_ap_poll.assert_called_once_with(2026, 2, 12)

    def test_writes_under_the_same_key_shape_backfill_uses(self):
        client = MagicMock()
        client.get_current_rankings_pointer.return_value = self._pointer_response()
        core_client = MagicMock()
        core_client.get_ap_poll.return_value = {"ranks": []}
        mock_s3 = MagicMock()

        with patch.object(ncaambb_ingest, "_s3", mock_s3):
            ncaambb_ingest._fetch_current_ap_poll(client, core_client)

        written_keys = [c.kwargs["Key"] for c in mock_s3.put_object.call_args_list]
        assert "ncaambb/rankings/2026/2/12.json" in written_keys

    def test_no_ap_pointer_is_a_no_op_not_an_error(self):
        client = MagicMock()
        client.get_current_rankings_pointer.return_value = {"rankings": []}
        core_client = MagicMock()

        result = ncaambb_ingest._fetch_current_ap_poll(client, core_client)

        assert result is False
        core_client.get_ap_poll.assert_not_called()

    def test_no_poll_for_the_resolved_week_is_a_no_op_not_an_error(self):
        client = MagicMock()
        client.get_current_rankings_pointer.return_value = self._pointer_response()
        core_client = MagicMock()
        core_client.get_ap_poll.return_value = None  # 404 -- no poll released this week

        with patch.object(ncaambb_ingest, "_s3", MagicMock()):
            result = ncaambb_ingest._fetch_current_ap_poll(client, core_client)

        assert result is False


class TestLambdaHandlerRankings:
    def test_a_ranking_fetch_failure_does_not_break_the_rest_of_ingest(self):
        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=())
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([])
        mock_client.get_current_rankings_pointer.side_effect = Exception("ESPN timeout")

        with patch.object(ncaambb_ingest, "NCAAMBBClient", return_value=mock_client), \
             patch.object(ncaambb_ingest, "_s3", MagicMock()):
            result = ncaambb_ingest.lambda_handler({"date": "20260114"}, None)

        assert result["rankings_fetched"] is False
        assert result["processed"] == 0  # the rest of the run still completed normally

    def test_rankings_fetched_flows_into_the_result(self):
        mock_client = MagicMock()
        mock_client.get_teams.return_value = _teams_response(team_ids=())
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([])
        mock_client.get_current_rankings_pointer.return_value = {
            "rankings": [{"type": "ap", "$ref": "http://x/.../seasons/2026/types/2/weeks/12/rankings/1"}]
        }
        mock_core_client = MagicMock()
        mock_core_client.get_ap_poll.return_value = {"ranks": []}

        with patch.object(ncaambb_ingest, "NCAAMBBClient", return_value=mock_client), \
             patch.object(ncaambb_ingest, "NCAAMBBCoreClient", return_value=mock_core_client), \
             patch.object(ncaambb_ingest, "_s3", MagicMock()):
            result = ncaambb_ingest.lambda_handler({"date": "20260114"}, None)

        assert result["rankings_fetched"] is True


class TestYesterday:
    def test_returns_the_day_before_today(self):
        assert ncaambb_ingest._yesterday(date(2026, 1, 15)) == "20260114"

    def test_crosses_a_month_boundary(self):
        assert ncaambb_ingest._yesterday(date(2026, 3, 1)) == "20260228"
