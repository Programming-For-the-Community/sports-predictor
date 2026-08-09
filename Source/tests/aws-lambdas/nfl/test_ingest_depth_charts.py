"""
Unit tests for the NFL ingest Lambda's daily, unconditional depth-chart
refresh (_fetch_depth_charts) -- same every-team, every-run cadence as
rosters (test_ingest_rosters.py), but cache-backed (get_cached_depth_chart's
own TTL) since position assignments don't need genuinely-daily freshness
the way roster membership does. Split out of what used to be one large
test_ingest.py -- see test_ingest_lambda_handler.py's own history note.

The nfl_ingest module is registered in sys.modules by conftest.py, which
also sets RAW_BUCKET_NAME before the module is imported (it's read at
module level by the handler).
"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import nfl_ingest

from _ingest_test_helpers import completed_event, make_client, make_core_client, make_s3, scoreboard, teams_response


class TestFetchDepthCharts:
    def test_fetches_and_caches_one_depth_chart_per_team(self):
        client = make_client(scoreboard([]))
        client.get_teams.return_value = teams_response("12", "13")
        mock_s3 = make_s3()

        with patch.object(nfl_ingest, "_s3", mock_s3):
            fetched, failed = nfl_ingest._fetch_depth_charts(client)

        assert fetched == 2
        assert failed == 0
        client.get_depth_chart.assert_any_call("12")
        client.get_depth_chart.assert_any_call("13")

    def test_fetches_every_team_regardless_of_any_weeks_schedule(self):
        # Same reasoning as test_ingest_rosters.py's own version of this
        # test -- sourced from get_teams, not any week's scoreboard, so
        # this covers all 32 regardless of who's playing.
        client = make_client(scoreboard([]))
        client.get_teams.return_value = teams_response(*[str(i) for i in range(1, 33)])
        mock_s3 = make_s3()

        with patch.object(nfl_ingest, "_s3", mock_s3):
            fetched, failed = nfl_ingest._fetch_depth_charts(client)

        assert fetched == 32

    def test_one_teams_failure_does_not_block_the_others(self):
        client = make_client(scoreboard([]))
        client.get_teams.return_value = teams_response("12", "13")
        client.get_depth_chart.side_effect = [Exception("boom"), {"depthchart": []}]
        mock_s3 = make_s3()

        with patch.object(nfl_ingest, "_s3", mock_s3):
            fetched, failed = nfl_ingest._fetch_depth_charts(client)

        assert fetched == 1
        assert failed == 1

    def test_fresh_cache_entry_skips_the_espn_call(self):
        # get_cached_depth_chart's own TTL, not a second cache layer here
        # -- confirms this function actually goes through the shared
        # cache instead of forcing a fetch every run.
        client = make_client(scoreboard([]))
        client.get_teams.return_value = teams_response("12")
        mock_s3 = make_s3()
        fresh = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        mock_s3.put_object(
            Key="nfl/cache/depth-charts/12.json",
            Body=json.dumps({"fetched_at": fresh, "data": {"qb": "cached"}}),
        )

        with patch.object(nfl_ingest, "_s3", mock_s3):
            fetched, failed = nfl_ingest._fetch_depth_charts(client)

        assert (fetched, failed) == (1, 0)
        client.get_depth_chart.assert_not_called()

    def test_no_teams_fetches_nothing(self):
        client = make_client(scoreboard([]))
        client.get_teams.return_value = teams_response()
        mock_s3 = make_s3()

        with patch.object(nfl_ingest, "_s3", mock_s3):
            fetched, failed = nfl_ingest._fetch_depth_charts(client)

        assert (fetched, failed) == (0, 0)
        client.get_depth_chart.assert_not_called()

    def test_lambda_handler_wires_depth_chart_fetch_into_its_own_run(self):
        board = scoreboard([completed_event("1")])
        mock_s3 = make_s3()
        mock_client = make_client(board)
        mock_client.get_teams.return_value = teams_response("12", "13")

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=make_core_client()):
            result = nfl_ingest.lambda_handler({}, None)

        assert result["depth_charts_fetched"] == 2
        assert result["depth_charts_failed"] == 0
