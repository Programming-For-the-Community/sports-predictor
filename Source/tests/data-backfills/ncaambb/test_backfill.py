"""
Unit tests for data-backfills/ncaambb/backfill.py's own logic -- everything
here is mocked, no real ESPN calls. Covers the date-based (not
week-based) walk and the ending-year season convention.

Unlike NBA's own test_backfill.py, there is no preseason-skip test here --
confirmed live, 2026-08-19 (see backfill.py's own docstring), NCAA MBB's
ESPN scoreboard has no preseason concept to skip, and process_date's
result dict has no "preseason_skipped" key for that reason.

process_date's own per-event loop runs on a ThreadPoolExecutor (see
backfill.py's own VOLUME docstring section), so mocks that need to
distinguish between events use an argument-aware side_effect (a function
keyed off the call's own argument) rather than a plain positional list --
submission order isn't guaranteed to match completion order.

The backfill module is importable directly (conftest.py inserts its
directory onto sys.path).
"""
from datetime import date
from unittest.mock import MagicMock, patch

import backfill


def _event(event_id="1", season_type=2, season_year=2026, completed=True):
    return {
        "id": event_id,
        "season": {"year": season_year, "type": season_type},
        "status": {"type": {"completed": completed}},
    }


class TestChunkSeasons:
    def test_splits_into_batches_of_the_given_size(self):
        assert backfill.chunk_seasons(2016, 2021, 2) == [[2016, 2017], [2018, 2019], [2020, 2021]]

    def test_uneven_final_batch_is_shorter(self):
        assert backfill.chunk_seasons(2016, 2020, 2) == [[2016, 2017], [2018, 2019], [2020]]

    def test_single_season_range(self):
        assert backfill.chunk_seasons(2025, 2025, 2) == [[2025]]


class TestSeasonDateRange:
    def test_starts_november_1_of_season_minus_1(self):
        days = backfill.season_date_range(2026)
        assert days[0] == date(2025, 11, 1)

    def test_ends_may_15_of_season(self):
        days = backfill.season_date_range(2026)
        assert days[-1] == date(2026, 5, 15)

    def test_every_date_is_consecutive(self):
        days = backfill.season_date_range(2026)
        for previous, current in zip(days, days[1:]):
            assert (current - previous).days == 1


class TestSeedTeams:
    def test_upserts_a_team_entity_per_team(self):
        client = MagicMock()
        storage = MagicMock()
        client.get_teams.return_value = {
            "sports": [{"leagues": [{"teams": [{"team": {"id": "1"}}, {"team": {"id": "2"}}]}]}]
        }

        with patch.object(backfill.normalize, "team_to_entity", side_effect=lambda t: {"entity_id": t["id"]}):
            backfill.seed_teams(client, storage)

        assert storage.upsert_entity.call_count == 2

    def test_writes_the_raw_payload_to_s3(self):
        client = MagicMock()
        storage = MagicMock()
        payload = {"sports": [{"leagues": [{"teams": []}]}]}
        client.get_teams.return_value = payload

        backfill.seed_teams(client, storage)

        storage.put_raw_json.assert_called_once_with("ncaambb/teams.json", payload)


class TestProcessDate:
    def test_no_events_returns_zeroed_result_without_writing(self):
        client = MagicMock()
        storage = MagicMock()
        client.get_scoreboard_for_date.return_value = {"events": []}

        result = backfill.process_date(client, storage, "20251101")

        assert result == {"games_processed": 0, "games_failed": 0, "failures": []}
        storage.put_raw_json.assert_not_called()

    def test_regular_season_date_writes_scoreboard_and_upserts_events(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = True  # skip box-score fetch, isolate this test to the event upsert
        events = [_event(event_id="1"), _event(event_id="2")]
        client.get_scoreboard_for_date.return_value = {"events": events}

        with patch.object(backfill.normalize, "scoreboard_event_to_event_item", side_effect=lambda e: {"event_id": e["id"]}):
            result = backfill.process_date(client, storage, "20260115")

        assert result["games_processed"] == 2
        assert result["games_failed"] == 0
        assert storage.upsert_event.call_count == 2
        storage.put_raw_json.assert_any_call("ncaambb/scoreboard/20260115.json", {"events": events})

    def test_incomplete_event_is_not_fetched_as_a_box_score(self):
        client = MagicMock()
        storage = MagicMock()
        client.get_scoreboard_for_date.return_value = {"events": [_event(completed=False)]}

        with patch.object(backfill.normalize, "scoreboard_event_to_event_item", return_value={}):
            result = backfill.process_date(client, storage, "20260115")

        assert result["games_processed"] == 1
        client.get_summary.assert_not_called()

    def test_one_event_failure_does_not_block_the_others(self):
        def _upsert_event(item):
            if item["event_id"] == "1":
                raise Exception("write failed")

        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = True
        events = [_event(event_id="1"), _event(event_id="2")]
        client.get_scoreboard_for_date.return_value = {"events": events}
        storage.upsert_event.side_effect = _upsert_event

        with patch.object(backfill.normalize, "scoreboard_event_to_event_item", side_effect=lambda e: {"event_id": e["id"]}):
            result = backfill.process_date(client, storage, "20260115")

        assert result["games_processed"] == 1
        assert result["games_failed"] == 1
        assert len(result["failures"]) == 1

    def test_a_full_night_of_events_all_process_successfully_via_the_thread_pool(self):
        # Volume check at roughly the confirmed real-world Saturday scale
        # (~150 games, see backfill.py's own VOLUME docstring section).
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = True
        events = [_event(event_id=str(i)) for i in range(150)]
        client.get_scoreboard_for_date.return_value = {"events": events}

        with patch.object(backfill.normalize, "scoreboard_event_to_event_item", side_effect=lambda e: {"event_id": e["id"]}):
            result = backfill.process_date(client, storage, "20260201")

        assert result["games_processed"] == 150
        assert result["games_failed"] == 0
        assert storage.upsert_event.call_count == 150


class TestSeedRankings:
    def test_writes_a_poll_for_every_week_that_has_one(self):
        core_client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = False
        core_client.get_ap_poll.return_value = {"ranks": []}

        written, absent = backfill.seed_rankings(core_client, storage, 2026)

        # Every probed week "has" a poll in this test (mock always returns
        # non-None) -- written should equal the full probed range, absent 0.
        total_weeks = sum(len(weeks) for weeks in backfill._RANKING_WEEKS_BY_TYPE.values())
        assert written == total_weeks
        assert absent == 0
        assert storage.put_raw_json.call_count == total_weeks

    def test_a_week_with_no_released_poll_is_not_written(self):
        core_client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = False
        core_client.get_ap_poll.return_value = None  # every week 404s

        written, absent = backfill.seed_rankings(core_client, storage, 2026)

        total_weeks = sum(len(weeks) for weeks in backfill._RANKING_WEEKS_BY_TYPE.values())
        assert written == 0
        assert absent == total_weeks
        storage.put_raw_json.assert_not_called()

    def test_already_seeded_week_is_not_refetched(self):
        core_client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = True  # every week "already in S3"

        written, absent = backfill.seed_rankings(core_client, storage, 2026)

        total_weeks = sum(len(weeks) for weeks in backfill._RANKING_WEEKS_BY_TYPE.values())
        assert written == total_weeks
        assert absent == 0
        core_client.get_ap_poll.assert_not_called()

    def test_writes_under_the_expected_key_shape(self):
        core_client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = False
        core_client.get_ap_poll.return_value = {"ranks": []}

        backfill.seed_rankings(core_client, storage, 2026)

        written_keys = {c.args[0] for c in storage.put_raw_json.call_args_list}
        assert "ncaambb/rankings/2026/2/1.json" in written_keys
        assert "ncaambb/rankings/2026/3/1.json" in written_keys


class TestProcessGame:
    def test_skips_fetch_when_box_score_already_exists(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = True

        backfill.process_game(client, storage, 2026, "401705127")

        client.get_summary.assert_not_called()

    def test_fetches_and_writes_when_missing(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = False
        client.get_summary.return_value = {"header": {}, "boxscore": {}}

        with patch.object(backfill.normalize, "boxscore_to_player_game_stats", return_value=([], [])), \
             patch.object(backfill.normalize, "boxscore_to_team_game_stats", return_value=[]):
            backfill.process_game(client, storage, 2026, "401705127")

        storage.put_raw_json.assert_called_once_with("ncaambb/boxscore/2026/401705127.json", client.get_summary.return_value)


class TestProcessBatch:
    def test_calls_seed_rankings_once_per_season_alongside_process_season(self):
        client = MagicMock()
        core_client = MagicMock()
        storage = MagicMock()
        client.get_scoreboard_for_date.return_value = {"events": []}

        with patch.object(backfill, "seed_rankings", return_value=(5, 1)) as mock_seed_rankings:
            results = backfill.process_batch(client, core_client, storage, [2025, 2026])

        assert mock_seed_rankings.call_count == 2
        mock_seed_rankings.assert_any_call(core_client, storage, 2025)
        mock_seed_rankings.assert_any_call(core_client, storage, 2026)
        assert all(r["rankings_written"] == 5 for r in results)
