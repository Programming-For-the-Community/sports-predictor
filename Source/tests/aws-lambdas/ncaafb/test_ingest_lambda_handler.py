"""
Unit tests for the NCAAFB ingest Lambda handler's core flow: season/week
resolution (explicit override vs. calendar auto-detection), the
teams/coaches cache refresh, and the completed-games gate on box-score
fetching. All AWS and CFBD calls are mocked.

Coach/ranking enrichment's own lookup logic is covered separately in
test_ingest_enrichment.py; this file patches enrich_games out entirely
via mock.

The ncaafb_ingest module is registered in sys.modules by conftest.py,
which also sets RAW_BUCKET_NAME before the module is imported.
"""
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

import ncaafb_ingest


def _game(game_id="1", home_id="2", away_id="52", completed=False, start_date="2025-09-28T20:25:00.000Z"):
    return {
        "id": game_id, "homeId": home_id, "awayId": away_id,
        "completed": completed, "startDate": start_date,
    }


def _calendar_week(season_type="regular", week=4, first_game_start="2025-09-25T00:00:00.000Z"):
    return {"seasonType": season_type, "week": week, "firstGameStart": first_game_start}


class TestLambdaHandlerWeekResolution:
    def test_uses_explicit_season_week_type_without_auto_detecting(self):
        # get_calendar is still called once here -- not for week
        # resolution (an explicit override skips that entirely, the thing
        # this test actually cares about), but because
        # _fetch_roster_if_stale's own _season_kickoff check runs on
        # every invocation regardless of override.
        mock_client = MagicMock()
        mock_client.get_games.return_value = []
        mock_client.get_calendar.return_value = []
        mock_s3, _ = _make_s3()

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "_s3", mock_s3), \
             patch.object(ncaafb_ingest, "get_cached_teams"), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches"), \
             patch.object(ncaafb_ingest.enrichment, "enrich_games"):
            ncaafb_ingest.lambda_handler({"season": 2024, "week": 3, "season_type": "postseason"}, None)

        mock_client.get_calendar.assert_called_once_with(2024)
        mock_client.get_games.assert_called_once_with(2024, week=3, season_type="postseason")

    def test_auto_detects_most_recently_started_week(self):
        mock_client = MagicMock()
        mock_client.get_games.return_value = []
        mock_client.get_calendar.return_value = [
            _calendar_week(week=3, first_game_start="2025-09-18T00:00:00.000Z"),
            _calendar_week(week=4, first_game_start="2025-09-25T00:00:00.000Z"),
            _calendar_week(week=5, first_game_start="2099-01-01T00:00:00.000Z"),  # not started yet
        ]

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "_s3", MagicMock()), \
             patch.object(ncaafb_ingest, "get_cached_teams"), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches"), \
             patch.object(ncaafb_ingest.enrichment, "enrich_games"):
            ncaafb_ingest.lambda_handler({}, None)

        mock_client.get_games.assert_called_once()
        assert mock_client.get_games.call_args.kwargs["week"] == 4

    def test_returns_early_when_no_week_has_started_yet(self):
        mock_client = MagicMock()
        mock_client.get_calendar.return_value = [
            _calendar_week(week=1, first_game_start="2099-01-01T00:00:00.000Z"),
        ]

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "get_cached_teams"), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches"):
            result = ncaafb_ingest.lambda_handler({}, None)

        assert result["processed"] == 0
        assert result["failed"] == 0
        mock_client.get_games.assert_not_called()

    def test_partial_override_still_triggers_auto_detection(self):
        # All three of season/week/season_type must be given together --
        # season alone isn't a full manual override.
        mock_client = MagicMock()
        mock_client.get_games.return_value = []
        mock_client.get_calendar.return_value = [_calendar_week(week=4)]
        mock_s3, _ = _make_s3()

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "_s3", mock_s3), \
             patch.object(ncaafb_ingest, "get_cached_teams"), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches"), \
             patch.object(ncaafb_ingest.enrichment, "enrich_games"):
            ncaafb_ingest.lambda_handler({"season": 2025}, None)

        # Twice -- once for _fetch_roster_if_stale's own _season_kickoff
        # check (every invocation), once for _resolve_current_week's real
        # auto-detection (season alone isn't a full override).
        assert mock_client.get_calendar.call_count == 2


class TestLambdaHandlerCacheRefresh:
    def test_teams_cached_true_on_success(self):
        mock_client = MagicMock()
        mock_client.get_games.return_value = []
        mock_client.get_calendar.return_value = []

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "get_cached_teams"), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches"):
            result = ncaafb_ingest.lambda_handler({}, None)

        assert result["teams_cached"] is True

    def test_teams_cached_false_on_failure_does_not_crash_the_run(self):
        mock_client = MagicMock()
        mock_client.get_calendar.return_value = []

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "get_cached_teams", side_effect=Exception("CFBD timeout")), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches"):
            result = ncaafb_ingest.lambda_handler({}, None)

        assert result["teams_cached"] is False

    def test_coaches_cached_false_on_failure_does_not_crash_the_run(self):
        mock_client = MagicMock()
        mock_client.get_calendar.return_value = []

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "get_cached_teams"), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches", side_effect=Exception("CFBD timeout")):
            result = ncaafb_ingest.lambda_handler({}, None)

        assert result["coaches_cached"] is False


class TestLambdaHandlerBoxScores:
    def test_no_completed_games_skips_box_score_fetch_entirely(self):
        mock_client = MagicMock()
        mock_client.get_games.return_value = [_game(completed=False)]
        mock_s3 = MagicMock()

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "_s3", mock_s3), \
             patch.object(ncaafb_ingest, "get_cached_teams"), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches"), \
             patch.object(ncaafb_ingest.enrichment, "enrich_games"):
            result = ncaafb_ingest.lambda_handler({"season": 2025, "week": 4, "season_type": "regular"}, None)

        assert result["processed"] == 0
        mock_client.get_game_player_stats.assert_not_called()
        mock_client.get_game_team_stats.assert_not_called()

    def test_completed_game_triggers_both_player_and_team_box_score_fetch(self):
        mock_client = MagicMock()
        mock_client.get_games.return_value = [_game(game_id="1", completed=True)]
        mock_client.get_game_player_stats.return_value = [{"id": "1"}]
        mock_client.get_game_team_stats.return_value = [{"id": "1"}]
        mock_s3 = MagicMock()

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "_s3", mock_s3), \
             patch.object(ncaafb_ingest, "get_cached_teams"), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches"), \
             patch.object(ncaafb_ingest.enrichment, "enrich_games"):
            result = ncaafb_ingest.lambda_handler({"season": 2025, "week": 4, "season_type": "regular"}, None)

        assert result["processed"] == 2
        assert result["failed"] == 0
        mock_client.get_game_player_stats.assert_called_once_with(2025, 4, "regular")
        mock_client.get_game_team_stats.assert_called_once_with(2025, 4, "regular")

    def test_box_scores_are_annotated_with_home_away_and_event_date(self):
        mock_client = MagicMock()
        mock_client.get_games.return_value = [
            _game(game_id="1", home_id="2", away_id="52", completed=True, start_date="2025-09-28T20:25:00.000Z"),
        ]
        mock_client.get_game_player_stats.return_value = [{"id": "1"}]
        mock_client.get_game_team_stats.return_value = [{"id": "1"}]
        mock_s3 = MagicMock()

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "_s3", mock_s3), \
             patch.object(ncaafb_ingest, "get_cached_teams"), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches"), \
             patch.object(ncaafb_ingest.enrichment, "enrich_games"):
            ncaafb_ingest.lambda_handler({"season": 2025, "week": 4, "season_type": "regular"}, None)

        written = {c.kwargs["Key"]: c.kwargs["Body"] for c in mock_s3.put_object.call_args_list}
        boxscore_body = written["ncaafb/boxscore/2025/regular/4.json"]
        assert b'"home_id": "2"' in boxscore_body
        assert b'"away_id": "52"' in boxscore_body
        assert b'"event_date": "2025-09-28"' in boxscore_body

    def test_box_score_event_date_for_a_late_kickoff_is_not_the_raw_utc_date(self):
        # 00:00 UTC is 8pm Eastern the day before -- must match
        # game_to_event_item's own event_date for the same game
        # ("2025-09-28"), not the raw UTC date ("2025-09-29").
        mock_client = MagicMock()
        mock_client.get_games.return_value = [
            _game(game_id="1", home_id="2", away_id="52", completed=True, start_date="2025-09-29T00:00:00.000Z"),
        ]
        mock_client.get_game_player_stats.return_value = [{"id": "1"}]
        mock_client.get_game_team_stats.return_value = [{"id": "1"}]
        mock_s3 = MagicMock()

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "_s3", mock_s3), \
             patch.object(ncaafb_ingest, "get_cached_teams"), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches"), \
             patch.object(ncaafb_ingest.enrichment, "enrich_games"):
            ncaafb_ingest.lambda_handler({"season": 2025, "week": 4, "season_type": "regular"}, None)

        written = {c.kwargs["Key"]: c.kwargs["Body"] for c in mock_s3.put_object.call_args_list}
        boxscore_body = written["ncaafb/boxscore/2025/regular/4.json"]
        assert b'"event_date": "2025-09-28"' in boxscore_body

    def test_player_box_score_failure_does_not_block_team_box_score(self):
        mock_client = MagicMock()
        mock_client.get_games.return_value = [_game(completed=True)]
        mock_client.get_game_player_stats.side_effect = Exception("CFBD timeout")
        mock_client.get_game_team_stats.return_value = [{"id": "1"}]
        mock_s3 = MagicMock()

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "_s3", mock_s3), \
             patch.object(ncaafb_ingest, "get_cached_teams"), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches"), \
             patch.object(ncaafb_ingest.enrichment, "enrich_games"):
            result = ncaafb_ingest.lambda_handler({"season": 2025, "week": 4, "season_type": "regular"}, None)

        assert result["processed"] == 1
        assert result["failed"] == 1
        mock_client.get_game_team_stats.assert_called_once()

    def test_games_are_written_before_box_scores_are_considered(self):
        mock_client = MagicMock()
        mock_client.get_games.return_value = [_game(completed=True)]
        mock_client.get_game_player_stats.return_value = []
        mock_client.get_game_team_stats.return_value = []
        mock_s3 = MagicMock()

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "_s3", mock_s3), \
             patch.object(ncaafb_ingest, "get_cached_teams"), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches"), \
             patch.object(ncaafb_ingest.enrichment, "enrich_games") as mock_enrich:
            ncaafb_ingest.lambda_handler({"season": 2025, "week": 4, "season_type": "regular"}, None)

        written_keys = [c.kwargs["Key"] for c in mock_s3.put_object.call_args_list]
        assert "ncaafb/games/2025/regular/4.json" in written_keys
        mock_enrich.assert_called_once()


def _make_s3():
    """Dict-backed fake S3 client -- a cache-miss GetObject raises
    ClientError, matching real boto3 behavior."""
    mock_s3 = MagicMock()
    store: dict[str, bytes] = {}

    def _get(**kwargs):
        key = kwargs.get("Key")
        if key in store:
            return {"Body": BytesIO(store[key])}
        raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "GetObject")

    def _put(**kwargs):
        body = kwargs.get("Body")
        store[kwargs.get("Key")] = body if isinstance(body, bytes) else body.encode("utf-8")

    mock_s3.get_object.side_effect = _get
    mock_s3.put_object.side_effect = _put
    return mock_s3, store


class TestSeasonKickoff:
    def test_returns_the_regular_season_week_1_first_game_start(self):
        client = MagicMock()
        client.get_calendar.return_value = [
            _calendar_week(season_type="regular", week=1, first_game_start="2026-08-23T00:00:00.000Z"),
            _calendar_week(season_type="regular", week=2, first_game_start="2026-09-05T00:00:00.000Z"),
        ]

        result = ncaafb_ingest._season_kickoff(client, 2026)

        assert result == datetime(2026, 8, 23, tzinfo=timezone.utc)

    def test_none_when_the_calendar_has_no_week_1_yet(self):
        client = MagicMock()
        client.get_calendar.return_value = [_calendar_week(season_type="regular", week=2)]

        assert ncaafb_ingest._season_kickoff(client, 2026) is None

    def test_ignores_a_postseason_week_1(self):
        # Postseason week numbering is flat and restarts at 1 -- not the
        # real regular-season kickoff.
        client = MagicMock()
        client.get_calendar.return_value = [
            _calendar_week(season_type="postseason", week=1, first_game_start="2025-12-01T00:00:00.000Z"),
            _calendar_week(season_type="regular", week=1, first_game_start="2026-08-23T00:00:00.000Z"),
        ]

        result = ncaafb_ingest._season_kickoff(client, 2026)

        assert result == datetime(2026, 8, 23, tzinfo=timezone.utc)


class TestRosterNeedsRefresh:
    def test_true_on_cache_miss(self):
        mock_s3, _ = _make_s3()
        with patch.object(ncaafb_ingest, "_s3", mock_s3):
            assert ncaafb_ingest._roster_needs_refresh(2025, None) is True

    def test_false_within_ttl_and_after_kickoff(self):
        mock_s3, store = _make_s3()
        fresh = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        store[ncaafb_ingest._roster_marker_key(2025)] = json.dumps({"fetched_at": fresh}).encode()
        with patch.object(ncaafb_ingest, "_s3", mock_s3):
            assert ncaafb_ingest._roster_needs_refresh(2025, None) is False

    def test_true_past_ttl(self):
        mock_s3, store = _make_s3()
        stale = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        store[ncaafb_ingest._roster_marker_key(2025)] = json.dumps({"fetched_at": stale}).encode()
        with patch.object(ncaafb_ingest, "_s3", mock_s3):
            assert ncaafb_ingest._roster_needs_refresh(2025, None) is True

    def test_true_on_malformed_marker(self):
        mock_s3, store = _make_s3()
        store[ncaafb_ingest._roster_marker_key(2025)] = b"not json"
        with patch.object(ncaafb_ingest, "_s3", mock_s3):
            assert ncaafb_ingest._roster_needs_refresh(2025, None) is True

    def test_true_when_fetched_before_season_kickoff_even_within_ttl(self):
        # Real complaint 2026-09-02: fetched during fall camp (2026-08-12),
        # season kicked off 2026-08-23 -- still well within the 30-day TTL
        # a week into the season, but the snapshot predates the real
        # season-opening roster (cuts/portal moves/walk-on promotions).
        mock_s3, store = _make_s3()
        fetched_during_camp = datetime(2026, 8, 12, tzinfo=timezone.utc)
        store[ncaafb_ingest._roster_marker_key(2025)] = json.dumps({"fetched_at": fetched_during_camp.isoformat()}).encode()
        kickoff = datetime(2026, 8, 23, tzinfo=timezone.utc)
        with patch.object(ncaafb_ingest, "_s3", mock_s3):
            assert ncaafb_ingest._roster_needs_refresh(2025, kickoff) is True

    def test_false_when_fetched_after_season_kickoff_and_within_ttl(self):
        mock_s3, store = _make_s3()
        fetched_after_kickoff = datetime.now(timezone.utc) - timedelta(days=1)
        store[ncaafb_ingest._roster_marker_key(2025)] = json.dumps({"fetched_at": fetched_after_kickoff.isoformat()}).encode()
        kickoff = fetched_after_kickoff - timedelta(days=3)
        with patch.object(ncaafb_ingest, "_s3", mock_s3):
            assert ncaafb_ingest._roster_needs_refresh(2025, kickoff) is False


class TestAnnotateRoster:
    def test_injects_team_id_resolved_by_school_name(self):
        roster = [{"id": "101", "team": "Georgia", "position": "QB"}]

        ncaafb_ingest._annotate_roster(roster, [{"id": 61, "school": "Georgia"}])

        assert roster[0]["teamId"] == 61

    def test_leaves_team_id_unset_when_school_not_found_in_teams_cache(self):
        roster = [{"id": "101", "team": "Unknown State", "position": "QB"}]

        ncaafb_ingest._annotate_roster(roster, [{"id": 61, "school": "Georgia"}])

        assert "teamId" not in roster[0]


class TestFetchRosterIfStale:
    def test_cache_miss_fetches_writes_both_roster_and_marker_with_team_id_resolved(self):
        mock_s3, store = _make_s3()
        client = MagicMock()
        client.get_calendar.return_value = []
        client.get_roster.return_value = [{"id": "1", "team": "Georgia", "position": "QB"}]
        teams = [{"id": 61, "school": "Georgia"}]

        with patch.object(ncaafb_ingest, "_s3", mock_s3):
            result = ncaafb_ingest._fetch_roster_if_stale(client, 2025, teams)

        assert result is True
        client.get_roster.assert_called_once_with(2025)
        roster_payload = json.loads(store["ncaafb/roster/2025.json"])
        assert roster_payload["data"] == [{"id": "1", "team": "Georgia", "position": "QB", "teamId": 61}]
        assert "fetched_at" in roster_payload
        assert ncaafb_ingest._roster_marker_key(2025) in store

    def test_fresh_cache_hit_skips_the_client_call(self):
        mock_s3, store = _make_s3()
        fresh = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        store[ncaafb_ingest._roster_marker_key(2025)] = json.dumps({"fetched_at": fresh}).encode()
        client = MagicMock()
        client.get_calendar.return_value = []

        with patch.object(ncaafb_ingest, "_s3", mock_s3):
            result = ncaafb_ingest._fetch_roster_if_stale(client, 2025, [])

        assert result is False
        client.get_roster.assert_not_called()

    def test_refetches_a_pre_kickoff_snapshot_even_though_it_is_within_the_ttl(self):
        # Real complaint 2026-09-02 -- see TestRosterNeedsRefresh's own
        # equivalent test for the full scenario this reproduces end to end.
        mock_s3, store = _make_s3()
        store[ncaafb_ingest._roster_marker_key(2025)] = json.dumps(
            {"fetched_at": datetime(2026, 8, 12, tzinfo=timezone.utc).isoformat()},
        ).encode()
        client = MagicMock()
        client.get_calendar.return_value = [_calendar_week(season_type="regular", week=1, first_game_start="2026-08-23T00:00:00.000Z")]
        client.get_roster.return_value = []

        with patch.object(ncaafb_ingest, "_s3", mock_s3):
            result = ncaafb_ingest._fetch_roster_if_stale(client, 2025, [])

        assert result is True
        client.get_roster.assert_called_once_with(2025)


class TestLambdaHandlerRosterFetch:
    def test_roster_fetched_true_reported_in_result(self):
        mock_client = MagicMock()
        mock_client.get_calendar.return_value = []

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "get_cached_teams"), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches"), \
             patch.object(ncaafb_ingest, "_fetch_roster_if_stale", return_value=True):
            result = ncaafb_ingest.lambda_handler({}, None)

        assert result["roster_fetched"] is True

    def test_roster_fetch_failure_does_not_crash_the_run(self):
        mock_client = MagicMock()
        mock_client.get_calendar.return_value = []

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "get_cached_teams"), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches"), \
             patch.object(ncaafb_ingest, "_fetch_roster_if_stale", side_effect=Exception("CFBD timeout")):
            result = ncaafb_ingest.lambda_handler({}, None)

        assert result["roster_fetched"] is False


class TestCurrentNcaafbSeason:
    def test_september_resolves_to_this_year(self):
        from datetime import date
        assert ncaafb_ingest._current_ncaafb_season(date(2025, 9, 1)) == 2025

    def test_january_resolves_to_last_year(self):
        from datetime import date
        assert ncaafb_ingest._current_ncaafb_season(date(2026, 1, 15)) == 2025

    def test_february_resolves_to_this_year(self):
        # Championship is done by late January -- Feb already means the
        # UPCOMING season.
        from datetime import date
        assert ncaafb_ingest._current_ncaafb_season(date(2026, 2, 1)) == 2026
