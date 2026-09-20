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

import pytest

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


class TestMain:
    def _fake_result(self, season, processed=1, failed=0, failures=None):
        return {
            "season": season, "games_processed": processed, "games_failed": failed,
            "failures": failures or [], "rankings_written": 0,
        }

    def test_seeds_teams_and_delegates_batches_to_process_batch(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["backfill.py", "--start-season", "2025", "--end-season", "2026", "--batch-size", "2"])
        mock_storage = MagicMock()

        with patch.object(backfill, "NCAAMBBClient"), \
             patch.object(backfill, "NCAAMBBCoreClient"), \
             patch.object(backfill, "PipelineStorage", return_value=mock_storage), \
             patch.object(backfill, "seed_teams") as mock_seed_teams, \
             patch.object(backfill, "process_batch", return_value=[self._fake_result(2025), self._fake_result(2026)]) as mock_process_batch:
            backfill.main()

        mock_seed_teams.assert_called_once()
        assert mock_process_batch.call_args.args[3] == [2025, 2026]

    def test_no_failures_does_not_exit_or_write_to_s3(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["backfill.py", "--start-season", "2026", "--end-season", "2026"])
        mock_storage = MagicMock()

        with patch.object(backfill, "NCAAMBBClient"), \
             patch.object(backfill, "NCAAMBBCoreClient"), \
             patch.object(backfill, "PipelineStorage", return_value=mock_storage), \
             patch.object(backfill, "seed_teams"), \
             patch.object(backfill, "process_batch", return_value=[self._fake_result(2026)]):
            backfill.main()

        mock_storage.put_raw_json.assert_not_called()

    def test_failures_are_written_to_s3_and_exit_code_is_1(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["backfill.py", "--start-season", "2026", "--end-season", "2026"])
        mock_storage = MagicMock()
        failure = {"date": "20260115", "event_id": "1", "error": "boom"}

        with patch.object(backfill, "NCAAMBBClient"), \
             patch.object(backfill, "NCAAMBBCoreClient"), \
             patch.object(backfill, "PipelineStorage", return_value=mock_storage), \
             patch.object(backfill, "seed_teams"), \
             patch.object(backfill, "process_batch", return_value=[self._fake_result(2026, processed=1, failed=1, failures=[failure])]):
            with pytest.raises(SystemExit) as exc_info:
                backfill.main()

        assert exc_info.value.code == 1
        key = mock_storage.put_raw_json.call_args.args[0]
        assert key.startswith("ncaambb/backfill-failures/")
        assert mock_storage.put_raw_json.call_args.args[1] == {"failures": [failure]}

    def test_a_batch_raising_does_not_stop_the_others_from_being_reported(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["backfill.py", "--start-season", "2025", "--end-season", "2026", "--batch-size", "1"])
        mock_storage = MagicMock()

        with patch.object(backfill, "NCAAMBBClient"), \
             patch.object(backfill, "NCAAMBBCoreClient"), \
             patch.object(backfill, "PipelineStorage", return_value=mock_storage), \
             patch.object(backfill, "seed_teams"), \
             patch.object(backfill, "process_batch", side_effect=[Exception("batch died"), [self._fake_result(2026)]]):
            backfill.main()  # should not raise despite one batch failing

        mock_storage.put_raw_json.assert_not_called()
