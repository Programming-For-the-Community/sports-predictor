"""
Unit tests for the NFL schedule-sync Lambda handler.

All AWS and ESPN calls are mocked -- these tests run without credentials.

The nfl_schedule_sync module is registered in sys.modules by conftest.py,
which also sets RAW_BUCKET_NAME before the module is imported (it's read
at module level by the handler).
"""
from datetime import date
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

import nfl_schedule_sync


def _make_client(scoreboard: dict | None = None):
    mock = MagicMock()
    mock.get_scoreboard.return_value = scoreboard or {"events": []}
    return mock


def _make_s3():
    """A stateless S3 mock is enough for most tests here -- they never
    read anything back. attach_depth_charts's own cache read/write DOES
    round-trip through get_object/put_object though, so the one test that
    exercises it (test_attaches_depth_charts_to_seeded_events) needs a
    real 404-until-written mock, same pattern as
    tests/library/storage/test_depth_chart_cache.py's own _make_s3."""
    mock_s3 = MagicMock()
    mock_s3.get_object.side_effect = ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "GetObject")
    return mock_s3


class TestLambdaHandler:
    def test_syncs_every_week_of_the_season(self):
        mock_s3 = MagicMock()
        mock_client = _make_client()

        with patch.object(nfl_schedule_sync, "_s3", mock_s3), \
             patch.object(nfl_schedule_sync, "NFLClient", return_value=mock_client):
            result = nfl_schedule_sync.lambda_handler({"season": 2026}, None)

        # 18 regular-season weeks + 5 postseason weeks.
        assert mock_client.get_scoreboard.call_count == 23
        assert result == {"season": 2026, "synced": 23, "failed": 0}

    def test_uses_one_shared_client_for_the_whole_run(self):
        # The whole point of this Lambda over the old per-week fan-out --
        # one NFLClient means one RateLimiter pacing every request in the
        # run, not 23 independent ones with no cross-invocation
        # coordination.
        mock_s3 = MagicMock()
        mock_client = _make_client()
        client_factory = MagicMock(return_value=mock_client)

        with patch.object(nfl_schedule_sync, "_s3", mock_s3), \
             patch.object(nfl_schedule_sync, "NFLClient", client_factory):
            nfl_schedule_sync.lambda_handler({"season": 2026}, None)

        client_factory.assert_called_once()

    def test_calls_regular_season_weeks_1_through_18(self):
        mock_s3 = MagicMock()
        mock_client = _make_client()

        with patch.object(nfl_schedule_sync, "_s3", mock_s3), \
             patch.object(nfl_schedule_sync, "NFLClient", return_value=mock_client):
            nfl_schedule_sync.lambda_handler({"season": 2026}, None)

        regular_calls = [c for c in mock_client.get_scoreboard.call_args_list if c.args[1] == 2]
        assert sorted(c.args[2] for c in regular_calls) == list(range(1, 19))

    def test_calls_postseason_weeks_1_through_5(self):
        mock_s3 = MagicMock()
        mock_client = _make_client()

        with patch.object(nfl_schedule_sync, "_s3", mock_s3), \
             patch.object(nfl_schedule_sync, "NFLClient", return_value=mock_client):
            nfl_schedule_sync.lambda_handler({"season": 2026}, None)

        postseason_calls = [c for c in mock_client.get_scoreboard.call_args_list if c.args[1] == 3]
        assert sorted(c.args[2] for c in postseason_calls) == list(range(1, 6))

    def test_writes_each_week_to_its_own_scoreboard_key(self):
        mock_s3 = MagicMock()
        mock_client = _make_client({"events": [{"id": "1"}]})

        with patch.object(nfl_schedule_sync, "_s3", mock_s3), \
             patch.object(nfl_schedule_sync, "NFLClient", return_value=mock_client):
            nfl_schedule_sync.lambda_handler({"season": 2026, "season_type": 2, "week": 1}, None)

        written_keys = [c.kwargs["Key"] for c in mock_s3.put_object.call_args_list]
        assert "nfl/scoreboard/2026/2/1.json" in written_keys

    def test_uses_explicit_season_from_event_payload(self):
        mock_s3 = MagicMock()
        mock_client = _make_client()

        with patch.object(nfl_schedule_sync, "_s3", mock_s3), \
             patch.object(nfl_schedule_sync, "NFLClient", return_value=mock_client), \
             patch.object(nfl_schedule_sync, "_current_nfl_season") as mock_season:
            nfl_schedule_sync.lambda_handler({"season": 2024}, None)

        mock_season.assert_not_called()
        assert mock_client.get_scoreboard.call_args_list[0].args[0] == 2024

    def test_resolves_season_from_calendar_when_omitted(self):
        mock_s3 = MagicMock()
        mock_client = _make_client()

        with patch.object(nfl_schedule_sync, "_s3", mock_s3), \
             patch.object(nfl_schedule_sync, "NFLClient", return_value=mock_client), \
             patch.object(nfl_schedule_sync, "_current_nfl_season", return_value=2027):
            result = nfl_schedule_sync.lambda_handler({}, None)

        assert result["season"] == 2027
        assert mock_client.get_scoreboard.call_args_list[0].args[0] == 2027

    def test_never_enriches_coach_injury_data_or_fetches_box_scores(self):
        # Coach/injury enrichment and box scores stay ingest-only --
        # confirm there's no EspnCoreApiClient/get_summary call anywhere
        # in this module. Depth charts ARE attached (see
        # test_attaches_depth_charts_to_seeded_events below).
        assert not hasattr(nfl_schedule_sync, "EspnCoreApiClient")
        assert not hasattr(nfl_schedule_sync, "_enrich_events")

    def test_attaches_depth_charts_to_seeded_events(self):
        event = {
            "id": "1",
            "competitions": [{"competitors": [
                {"homeAway": "home", "team": {"id": "12"}},
                {"homeAway": "away", "team": {"id": "7"}},
            ]}],
        }
        mock_s3 = _make_s3()
        mock_client = _make_client({"events": [event]})
        mock_client.get_depth_chart.side_effect = lambda team_id: {
            "depthchart": [{"id": "1", "name": "group", "positions": {
                "qb": {"position": {"abbreviation": "QB"}, "athletes": [{"id": f"qb-{team_id}"}]},
            }}],
        }

        with patch.object(nfl_schedule_sync, "_s3", mock_s3), \
             patch.object(nfl_schedule_sync, "NFLClient", return_value=mock_client):
            nfl_schedule_sync.lambda_handler({"season": 2026, "season_type": 2, "week": 1}, None)

        assert event["home_depth_chart"] == {"qb": {"position": {"abbreviation": "QB"}, "athletes": [{"id": "qb-12"}]}}
        assert event["away_depth_chart"] == {"qb": {"position": {"abbreviation": "QB"}, "athletes": [{"id": "qb-7"}]}}

    def test_one_weeks_failure_does_not_block_the_rest(self):
        mock_s3 = MagicMock()
        mock_client = _make_client()
        mock_client.get_scoreboard.side_effect = [
            Exception("ESPN timeout"), *([{"events": []}] * 22),
        ]

        with patch.object(nfl_schedule_sync, "_s3", mock_s3), \
             patch.object(nfl_schedule_sync, "NFLClient", return_value=mock_client):
            result = nfl_schedule_sync.lambda_handler({"season": 2026}, None)

        assert result == {"season": 2026, "synced": 22, "failed": 1}
        assert mock_client.get_scoreboard.call_count == 23


class TestCurrentNflSeason:
    def test_september_resolves_to_this_year(self):
        assert nfl_schedule_sync._current_nfl_season(date(2026, 9, 1)) == 2026

    def test_december_resolves_to_this_year(self):
        assert nfl_schedule_sync._current_nfl_season(date(2026, 12, 31)) == 2026

    def test_january_resolves_to_last_year(self):
        # Still finishing that season's playoffs.
        assert nfl_schedule_sync._current_nfl_season(date(2027, 1, 15)) == 2026

    def test_february_resolves_to_last_year(self):
        # The Super Bowl is in February.
        assert nfl_schedule_sync._current_nfl_season(date(2027, 2, 8)) == 2026

    def test_march_resolves_to_this_year(self):
        # The upcoming, not-yet-started season -- this is what lets an
        # off-season run seed next season's schedule before Week 1.
        assert nfl_schedule_sync._current_nfl_season(date(2027, 3, 1)) == 2027

    def test_august_resolves_to_this_year(self):
        assert nfl_schedule_sync._current_nfl_season(date(2027, 8, 31)) == 2027

    def test_defaults_to_actual_today_when_not_given(self):
        result = nfl_schedule_sync._current_nfl_season()
        assert isinstance(result, int)
