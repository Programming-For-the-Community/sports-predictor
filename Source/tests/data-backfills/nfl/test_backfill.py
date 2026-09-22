"""
Unit tests for data-backfills/nfl/backfill.py's own logic -- everything
here is mocked, no real ESPN calls. Covers the week-based (season_type x
week) walk and the per-event/per-batch failure resilience.

The backfill module is importable directly (conftest.py inserts its
directory onto sys.path).
"""
from unittest.mock import MagicMock, patch

import pytest

import backfill


def _event(event_id="1"):
    return {"id": event_id}


class TestChunkSeasons:
    def test_splits_into_batches_of_the_given_size(self):
        assert backfill.chunk_seasons(2016, 2021, 2) == [[2016, 2017], [2018, 2019], [2020, 2021]]

    def test_uneven_final_batch_is_shorter(self):
        assert backfill.chunk_seasons(2016, 2020, 2) == [[2016, 2017], [2018, 2019], [2020]]

    def test_single_season_range(self):
        assert backfill.chunk_seasons(2025, 2025, 2) == [[2025]]


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

        storage.put_raw_json.assert_called_once_with("nfl/teams.json", payload)


class TestProcessGame:
    def test_skips_fetch_when_box_score_already_exists(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = True

        backfill.process_game(client, storage, 2025, "401547603")

        client.get_summary.assert_not_called()

    def test_fetches_and_writes_when_missing(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = False
        client.get_summary.return_value = {"header": {}, "boxscore": {}}

        with patch.object(backfill.normalize, "boxscore_to_player_game_stats", return_value=([], [])), \
             patch.object(backfill.normalize, "boxscore_to_team_game_stats", return_value=[]):
            backfill.process_game(client, storage, 2025, "401547603")

        storage.put_raw_json.assert_called_once_with("nfl/boxscore/2025/401547603.json", client.get_summary.return_value)

    def test_writes_player_and_team_stats_from_the_normalized_box_score(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = False
        client.get_summary.return_value = {"header": {}, "boxscore": {}}
        player_entities = [{"entity_id": "9001"}]

        with patch.object(backfill.normalize, "boxscore_to_player_game_stats", return_value=([{"pk": "stat1"}], player_entities)), \
             patch.object(backfill.normalize, "boxscore_to_team_game_stats", return_value=[{"pk": "team-stat1"}]):
            backfill.process_game(client, storage, 2025, "401547603")

        storage.upsert_player_entity.assert_called_once_with(player_entities[0])
        storage.write_player_game_stats.assert_called_once_with([{"pk": "stat1"}])
        storage.write_team_game_stats.assert_called_once_with([{"pk": "team-stat1"}])


class TestProcessSeason:
    def test_writes_a_scoreboard_and_upserts_events_for_every_week(self):
        client = MagicMock()
        storage = MagicMock()
        client.get_scoreboard.return_value = {"events": [_event("1")]}
        storage.raw_object_exists.return_value = True  # skip box-score fetch, isolate this test to the event upsert

        with patch.object(backfill.normalize, "scoreboard_event_to_event_item", side_effect=lambda e: {"event_id": e["id"]}):
            result = backfill.process_season(client, storage, 2025)

        expected_weeks = len(backfill.REGULAR_SEASON_WEEKS) + len(backfill.POSTSEASON_WEEKS)
        assert client.get_scoreboard.call_count == expected_weeks
        assert result["season"] == 2025
        assert result["games_processed"] == expected_weeks
        assert result["games_failed"] == 0

    def test_calls_every_regular_season_week_with_season_type_2(self):
        client = MagicMock()
        storage = MagicMock()
        client.get_scoreboard.return_value = {"events": []}

        backfill.process_season(client, storage, 2025)

        regular_calls = [c for c in client.get_scoreboard.call_args_list if c.args[1] == 2]
        assert sorted(c.args[2] for c in regular_calls) == list(backfill.REGULAR_SEASON_WEEKS)

    def test_calls_every_postseason_week_with_season_type_3(self):
        client = MagicMock()
        storage = MagicMock()
        client.get_scoreboard.return_value = {"events": []}

        backfill.process_season(client, storage, 2025)

        postseason_calls = [c for c in client.get_scoreboard.call_args_list if c.args[1] == 3]
        assert sorted(c.args[2] for c in postseason_calls) == list(backfill.POSTSEASON_WEEKS)

    def test_a_week_with_no_events_writes_the_scoreboard_but_upserts_nothing(self):
        client = MagicMock()
        storage = MagicMock()
        client.get_scoreboard.return_value = {"events": []}

        result = backfill.process_season(client, storage, 2025)

        assert result["games_processed"] == 0
        storage.upsert_event.assert_not_called()

    def test_one_events_failure_does_not_block_the_rest_of_that_week(self):
        client = MagicMock()
        storage = MagicMock()
        # Only the very first get_scoreboard call (week 1, regular season)
        # returns real events -- every other week is empty, isolating the
        # failure-resilience assertions below to that one week's 2 events.
        events_week = {"events": [_event("1"), _event("2")]}
        empty_week = {"events": []}
        total_weeks = len(backfill.REGULAR_SEASON_WEEKS) + len(backfill.POSTSEASON_WEEKS)
        client.get_scoreboard.side_effect = [events_week] + [empty_week] * (total_weeks - 1)
        storage.raw_object_exists.return_value = True
        storage.upsert_event.side_effect = [Exception("write failed"), None]

        with patch.object(backfill.normalize, "scoreboard_event_to_event_item", side_effect=lambda e: {"event_id": e["id"]}):
            result = backfill.process_season(client, storage, 2025)

        assert result["games_processed"] == 1
        assert result["games_failed"] == 1
        assert result["failures"] == [{"season": 2025, "event_id": "1", "error": "write failed"}]


class TestProcessBatch:
    def test_processes_every_season_and_collects_results(self):
        client = MagicMock()
        storage = MagicMock()
        season_results = {
            2024: {"season": 2024, "games_processed": 5, "games_failed": 0, "failures": []},
            2025: {"season": 2025, "games_processed": 3, "games_failed": 1, "failures": []},
        }

        with patch.object(backfill, "process_season", side_effect=lambda c, s, season: season_results[season]):
            results = backfill.process_batch(client, storage, [2024, 2025])

        assert results == [season_results[2024], season_results[2025]]


class TestMain:
    def _fake_result(self, season, processed=1, failed=0, failures=None):
        return {"season": season, "games_processed": processed, "games_failed": failed, "failures": failures or []}

    def test_seeds_teams_and_delegates_batches_to_process_batch(self, monkeypatch):
        monkeypatch.setenv("START_SEASON", "2024")
        monkeypatch.setenv("END_SEASON", "2025")
        monkeypatch.setenv("BATCH_SIZE", "2")
        monkeypatch.setattr("sys.argv", ["backfill.py"])
        mock_storage = MagicMock()

        with patch.object(backfill, "NFLClient"), \
             patch.object(backfill, "PipelineStorage", return_value=mock_storage), \
             patch.object(backfill, "seed_teams") as mock_seed_teams, \
             patch.object(backfill, "process_batch", return_value=[self._fake_result(2024), self._fake_result(2025)]) as mock_process_batch:
            backfill.main()

        mock_seed_teams.assert_called_once()
        mock_process_batch.assert_called_once_with(mock_seed_teams.call_args.args[0], mock_storage, [2024, 2025])

    def test_no_failures_does_not_exit_or_write_to_s3(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["backfill.py", "--start-season", "2025", "--end-season", "2025"])
        mock_storage = MagicMock()

        with patch.object(backfill, "NFLClient"), \
             patch.object(backfill, "PipelineStorage", return_value=mock_storage), \
             patch.object(backfill, "seed_teams"), \
             patch.object(backfill, "process_batch", return_value=[self._fake_result(2025)]):
            backfill.main()

        mock_storage.put_raw_json.assert_not_called()

    def test_failures_are_written_to_s3_and_exit_code_is_1(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["backfill.py", "--start-season", "2025", "--end-season", "2025"])
        mock_storage = MagicMock()
        failure = {"season": 2025, "event_id": "1", "error": "boom"}

        with patch.object(backfill, "NFLClient"), \
             patch.object(backfill, "PipelineStorage", return_value=mock_storage), \
             patch.object(backfill, "seed_teams"), \
             patch.object(backfill, "process_batch", return_value=[self._fake_result(2025, processed=1, failed=1, failures=[failure])]):
            with pytest.raises(SystemExit) as exc_info:
                backfill.main()

        assert exc_info.value.code == 1
        key = mock_storage.put_raw_json.call_args.args[0]
        assert key.startswith("nfl/backfill-failures/")
        assert mock_storage.put_raw_json.call_args.args[1] == {"failures": [failure]}

    def test_a_batch_raising_does_not_stop_the_others_from_being_reported(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["backfill.py", "--start-season", "2024", "--end-season", "2025", "--batch-size", "1"])
        mock_storage = MagicMock()

        with patch.object(backfill, "NFLClient"), \
             patch.object(backfill, "PipelineStorage", return_value=mock_storage), \
             patch.object(backfill, "seed_teams"), \
             patch.object(backfill, "process_batch", side_effect=[Exception("batch died"), [self._fake_result(2025)]]):
            backfill.main()  # should not raise despite one batch failing

        mock_storage.put_raw_json.assert_not_called()
