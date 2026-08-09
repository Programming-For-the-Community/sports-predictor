"""
Unit tests for the NFL ingest Lambda's daily, unconditional, uncached
roster fetch (_fetch_rosters) -- one team at a time from _all_team_ids,
independent of any week's own scoreboard (so it covers every team, not
just whoever's playing that week, and runs even during preseason -- see
_fetch_rosters' own docstring). Split out of what used to be one large
test_ingest.py -- see test_ingest_lambda_handler.py's own history note.

The nfl_ingest module is registered in sys.modules by conftest.py, which
also sets RAW_BUCKET_NAME before the module is imported (it's read at
module level by the handler).
"""
from unittest.mock import patch

import nfl_ingest

from _ingest_test_helpers import completed_event, make_client, make_core_client, make_s3, scoreboard, teams_response


class TestFetchRosters:
    def test_fetches_and_writes_one_roster_per_team(self):
        client = make_client(scoreboard([]))
        client.get_teams.return_value = teams_response("12", "13")
        mock_s3 = make_s3()

        with patch.object(nfl_ingest, "_s3", mock_s3):
            fetched, failed = nfl_ingest._fetch_rosters(client)

        assert fetched == 2
        assert failed == 0
        client.get_roster.assert_any_call("12")
        client.get_roster.assert_any_call("13")
        assert client.get_roster.call_count == 2

    def test_fetches_every_team_regardless_of_any_weeks_schedule(self):
        # The whole point of sourcing team ids from get_teams instead of a
        # week's scoreboard -- this covers all 32, not just whichever
        # teams happen to have a game that week (or any games at all).
        client = make_client(scoreboard([]))
        client.get_teams.return_value = teams_response(*[str(i) for i in range(1, 33)])
        mock_s3 = make_s3()

        with patch.object(nfl_ingest, "_s3", mock_s3):
            fetched, failed = nfl_ingest._fetch_rosters(client)

        assert fetched == 32

    def test_writes_to_the_expected_s3_key(self):
        client = make_client(scoreboard([]))
        client.get_teams.return_value = teams_response("12", "13")
        mock_s3 = make_s3()

        with patch.object(nfl_ingest, "_s3", mock_s3):
            nfl_ingest._fetch_rosters(client)

        written_keys = {call.kwargs["Key"] for call in mock_s3.put_object.call_args_list}
        assert written_keys == {"nfl/roster/12.json", "nfl/roster/13.json"}

    def test_one_teams_failure_does_not_block_the_others(self):
        client = make_client(scoreboard([]))
        client.get_teams.return_value = teams_response("12", "13")
        client.get_roster.side_effect = [Exception("boom"), {"team": {"id": "13"}, "timestamp": "2026-08-08T00:00:00Z", "athletes": []}]
        mock_s3 = make_s3()

        with patch.object(nfl_ingest, "_s3", mock_s3):
            fetched, failed = nfl_ingest._fetch_rosters(client)

        assert fetched == 1
        assert failed == 1

    def test_no_teams_fetches_nothing(self):
        client = make_client(scoreboard([]))
        client.get_teams.return_value = teams_response()
        mock_s3 = make_s3()

        with patch.object(nfl_ingest, "_s3", mock_s3):
            fetched, failed = nfl_ingest._fetch_rosters(client)

        assert (fetched, failed) == (0, 0)
        client.get_roster.assert_not_called()

    def test_lambda_handler_wires_roster_fetch_into_its_own_run(self):
        board = scoreboard([completed_event("1")])
        mock_s3 = make_s3()
        mock_client = make_client(board)
        mock_client.get_teams.return_value = teams_response("12", "13")

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=make_core_client()):
            result = nfl_ingest.lambda_handler({}, None)

        assert result["rosters_fetched"] == 2
        assert result["rosters_failed"] == 0
