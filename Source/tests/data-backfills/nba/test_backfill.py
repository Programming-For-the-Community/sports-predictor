"""
Unit tests for data-backfills/nba/backfill.py's own logic -- everything
here is mocked, no real ESPN calls. Covers the date-based (not
week-based) walk, the ending-year season convention, and the
preseason/incomplete-game skip branches in process_date.

The backfill module is importable directly (conftest.py inserts its
directory onto sys.path).
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

import backfill


def _event(event_id="1", season_type=2, season_year=2025, completed=True):
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
    def test_starts_october_1_of_season_minus_1(self):
        days = backfill.season_date_range(2025)
        assert days[0] == date(2024, 10, 1)

    def test_ends_june_30_of_season(self):
        days = backfill.season_date_range(2025)
        assert days[-1] == date(2025, 6, 30)

    def test_every_date_is_consecutive(self):
        days = backfill.season_date_range(2025)
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

        storage.put_raw_json.assert_called_once_with("nba/teams.json", payload)


class TestProcessDate:
    def test_no_events_returns_zeroed_result_without_writing(self):
        client = MagicMock()
        storage = MagicMock()
        client.get_scoreboard_for_date.return_value = {"events": []}

        result = backfill.process_date(client, storage, "20241001")

        assert result == {"games_processed": 0, "games_failed": 0, "preseason_skipped": False, "failures": []}
        storage.put_raw_json.assert_not_called()

    def test_preseason_date_is_skipped_without_writing(self):
        client = MagicMock()
        storage = MagicMock()
        client.get_scoreboard_for_date.return_value = {"events": [_event(season_type=1)]}

        result = backfill.process_date(client, storage, "20241010")

        assert result["preseason_skipped"] is True
        assert result["games_processed"] == 0
        storage.put_raw_json.assert_not_called()
        storage.upsert_event.assert_not_called()

    def test_regular_season_date_writes_scoreboard_and_upserts_events(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = True  # skip box-score fetch, isolate this test to the event upsert
        events = [_event(event_id="1"), _event(event_id="2")]
        client.get_scoreboard_for_date.return_value = {"events": events}

        with patch.object(backfill.normalize, "scoreboard_event_to_event_item", side_effect=lambda e: {"event_id": e["id"]}):
            result = backfill.process_date(client, storage, "20250115")

        assert result["games_processed"] == 2
        assert result["games_failed"] == 0
        assert storage.upsert_event.call_count == 2
        storage.put_raw_json.assert_any_call("nba/scoreboard/20250115.json", {"events": events})

    def test_incomplete_event_is_not_fetched_as_a_box_score(self):
        client = MagicMock()
        storage = MagicMock()
        client.get_scoreboard_for_date.return_value = {"events": [_event(completed=False)]}

        with patch.object(backfill.normalize, "scoreboard_event_to_event_item", return_value={}):
            result = backfill.process_date(client, storage, "20250115")

        assert result["games_processed"] == 1
        client.get_summary.assert_not_called()

    def test_one_event_failure_does_not_block_the_others(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = True
        events = [_event(event_id="1"), _event(event_id="2")]
        client.get_scoreboard_for_date.return_value = {"events": events}
        storage.upsert_event.side_effect = [Exception("write failed"), None]

        with patch.object(backfill.normalize, "scoreboard_event_to_event_item", side_effect=lambda e: {"event_id": e["id"]}):
            result = backfill.process_date(client, storage, "20250115")

        assert result["games_processed"] == 1
        assert result["games_failed"] == 1
        assert len(result["failures"]) == 1


class TestProcessGame:
    def test_skips_fetch_when_box_score_already_exists(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = True

        backfill.process_game(client, storage, 2025, "401705127")

        client.get_summary.assert_not_called()

    def test_fetches_and_writes_when_missing(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = False
        client.get_summary.return_value = {"header": {}, "boxscore": {}}

        with patch.object(backfill.normalize, "boxscore_to_player_game_stats", return_value=([], [])), \
             patch.object(backfill.normalize, "boxscore_to_team_game_stats", return_value=[]):
            backfill.process_game(client, storage, 2025, "401705127")

        storage.put_raw_json.assert_called_once_with("nba/boxscore/2025/401705127.json", client.get_summary.return_value)


class TestProcessSeason:
    def test_sums_results_across_every_date_in_the_season(self):
        client = MagicMock()
        storage = MagicMock()
        results = [
            {"games_processed": 1, "games_failed": 0, "preseason_skipped": True, "failures": []},
            {"games_processed": 2, "games_failed": 1, "preseason_skipped": False, "failures": [{"event_id": "1"}]},
        ]

        with patch.object(backfill, "season_date_range", return_value=[date(2024, 10, 1), date(2024, 10, 2)]), \
             patch.object(backfill, "process_date", side_effect=results):
            result = backfill.process_season(client, storage, 2025)

        assert result["season"] == 2025
        assert result["games_processed"] == 3
        assert result["games_failed"] == 1
        assert result["dates_skipped_preseason"] == 1
        assert result["failures"] == [{"event_id": "1"}]

    def test_formats_each_date_as_yyyymmdd_for_process_date(self):
        client = MagicMock()
        storage = MagicMock()

        with patch.object(backfill, "season_date_range", return_value=[date(2024, 10, 1)]), \
             patch.object(backfill, "process_date", return_value={
                 "games_processed": 0, "games_failed": 0, "preseason_skipped": False, "failures": [],
             }) as mock_process_date:
            backfill.process_season(client, storage, 2025)

        mock_process_date.assert_called_once_with(client, storage, "20241001")


class TestProcessBatch:
    def test_processes_every_season_and_collects_results(self):
        client = MagicMock()
        storage = MagicMock()
        season_results = {
            2024: {"season": 2024, "games_processed": 5, "games_failed": 0, "dates_skipped_preseason": 0, "failures": []},
            2025: {"season": 2025, "games_processed": 3, "games_failed": 1, "dates_skipped_preseason": 0, "failures": []},
        }

        with patch.object(backfill, "process_season", side_effect=lambda c, s, season: season_results[season]):
            results = backfill.process_batch(client, storage, [2024, 2025])

        assert results == [season_results[2024], season_results[2025]]


class TestMain:
    def _fake_result(self, season, processed=1, failed=0, failures=None):
        return {
            "season": season, "games_processed": processed, "games_failed": failed,
            "dates_skipped_preseason": 0, "failures": failures or [],
        }

    def test_seeds_teams_and_delegates_batches_to_process_batch(self, monkeypatch):
        monkeypatch.setenv("START_SEASON", "2024")
        monkeypatch.setenv("END_SEASON", "2025")
        monkeypatch.setenv("BATCH_SIZE", "2")
        monkeypatch.setattr("sys.argv", ["backfill.py"])
        mock_storage = MagicMock()

        with patch.object(backfill, "NBAClient"), \
             patch.object(backfill, "PipelineStorage", return_value=mock_storage), \
             patch.object(backfill, "seed_teams") as mock_seed_teams, \
             patch.object(backfill, "process_batch", return_value=[self._fake_result(2024), self._fake_result(2025)]) as mock_process_batch:
            backfill.main()

        mock_seed_teams.assert_called_once()
        mock_process_batch.assert_called_once_with(mock_seed_teams.call_args.args[0], mock_storage, [2024, 2025])

    def test_no_failures_does_not_exit_or_write_to_s3(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["backfill.py", "--start-season", "2025", "--end-season", "2025"])
        mock_storage = MagicMock()

        with patch.object(backfill, "NBAClient"), \
             patch.object(backfill, "PipelineStorage", return_value=mock_storage), \
             patch.object(backfill, "seed_teams"), \
             patch.object(backfill, "process_batch", return_value=[self._fake_result(2025)]):
            backfill.main()

        mock_storage.put_raw_json.assert_not_called()

    def test_failures_are_written_to_s3_and_exit_code_is_1(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["backfill.py", "--start-season", "2025", "--end-season", "2025"])
        mock_storage = MagicMock()
        failure = {"date": "20250115", "event_id": "1", "error": "boom"}

        with patch.object(backfill, "NBAClient"), \
             patch.object(backfill, "PipelineStorage", return_value=mock_storage), \
             patch.object(backfill, "seed_teams"), \
             patch.object(backfill, "process_batch", return_value=[self._fake_result(2025, processed=1, failed=1, failures=[failure])]):
            with pytest.raises(SystemExit) as exc_info:
                backfill.main()

        assert exc_info.value.code == 1
        key = mock_storage.put_raw_json.call_args.args[0]
        assert key.startswith("nba/backfill-failures/")
        assert mock_storage.put_raw_json.call_args.args[1] == {"failures": [failure]}

    def test_a_batch_raising_does_not_stop_the_others_from_being_reported(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["backfill.py", "--start-season", "2024", "--end-season", "2025", "--batch-size", "1"])
        mock_storage = MagicMock()

        with patch.object(backfill, "NBAClient"), \
             patch.object(backfill, "PipelineStorage", return_value=mock_storage), \
             patch.object(backfill, "seed_teams"), \
             patch.object(backfill, "process_batch", side_effect=[Exception("batch died"), [self._fake_result(2025)]]):
            backfill.main()  # should not raise despite one batch failing

        mock_storage.put_raw_json.assert_not_called()
