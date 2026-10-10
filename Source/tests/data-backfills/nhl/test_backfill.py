"""
Unit tests for data-backfills/nhl/backfill.py's own logic -- everything
here is mocked, no real ESPN calls. Covers the date walk, the
ending-year season convention, and the preseason/exhibition/
incomplete-game skip branches in process_date.

The backfill module is importable directly (conftest.py inserts its
directory onto sys.path).
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

import backfill


def _event(event_id="1", season_type=2, season_year=2026, completed=True, home_id="13", away_id="2"):
    return {
        "id": event_id,
        "season": {"year": season_year, "type": season_type},
        "status": {"type": {"completed": completed}},
        "competitions": [{"competitors": [
            {"homeAway": "home", "team": {"id": home_id}},
            {"homeAway": "away", "team": {"id": away_id}},
        ]}],
    }


def _date_result(processed=0, failed=0, skipped=0, failures=None):
    return {"games_processed": processed, "games_failed": failed, "games_skipped": skipped, "failures": failures or []}


class TestChunkSeasons:
    def test_splits_into_batches_of_the_given_size(self):
        assert backfill.chunk_seasons(2016, 2021, 2) == [[2016, 2017], [2018, 2019], [2020, 2021]]

    def test_uneven_final_batch_is_shorter(self):
        assert backfill.chunk_seasons(2016, 2020, 2) == [[2016, 2017], [2018, 2019], [2020]]


class TestSeasonDateRange:
    def test_runs_october_1_of_season_minus_1_through_september_30(self):
        days = backfill.season_date_range(2020, today=date(2026, 10, 9))

        assert days[0] == date(2019, 10, 1)
        assert days[-1] == date(2020, 9, 30)  # the 2020 playoffs ended September 28

    def test_every_date_is_consecutive(self):
        days = backfill.season_date_range(2025, today=date(2026, 10, 9))
        for previous, current in zip(days, days[1:]):
            assert (current - previous).days == 1

    def test_current_season_stops_at_today(self):
        days = backfill.season_date_range(2027, today=date(2026, 10, 9))

        assert days[0] == date(2026, 10, 1)
        assert days[-1] == date(2026, 10, 9)

    def test_a_season_that_has_not_started_has_no_dates(self):
        assert backfill.season_date_range(2028, today=date(2026, 10, 9)) == []


class TestSeedTeams:
    def test_upserts_a_team_entity_per_team_and_writes_the_raw_payload(self):
        client = MagicMock()
        storage = MagicMock()
        payload = {"sports": [{"leagues": [{"teams": [{"team": {"id": "1"}}, {"team": {"id": "2"}}]}]}]}
        client.get_teams.return_value = payload

        with patch.object(backfill.normalize, "team_to_entity", side_effect=lambda t: {"entity_id": t["id"]}):
            backfill.seed_teams(client, storage)

        assert storage.upsert_entity.call_count == 2
        storage.put_raw_json.assert_called_once_with("nhl/teams.json", payload)


class TestProcessDate:
    def test_no_events_returns_zeroed_result_without_writing(self):
        client = MagicMock()
        storage = MagicMock()
        client.get_scoreboard_for_date.return_value = {"events": []}

        assert backfill.process_date(client, storage, "20250801") == _date_result()
        storage.put_raw_json.assert_not_called()

    def test_preseason_date_is_skipped_without_writing(self):
        client = MagicMock()
        storage = MagicMock()
        client.get_scoreboard_for_date.return_value = {"events": [_event(season_type=1)]}

        result = backfill.process_date(client, storage, "20250926")

        assert result == _date_result(skipped=1)
        storage.put_raw_json.assert_not_called()
        storage.upsert_event.assert_not_called()

    def test_all_star_game_is_skipped_and_its_box_score_never_fetched(self):
        client = MagicMock()
        storage = MagicMock()
        client.get_scoreboard_for_date.return_value = {"events": [_event(home_id="129030", away_id="129031")]}

        result = backfill.process_date(client, storage, "20240203")

        assert result == _date_result(skipped=1)
        client.get_summary.assert_not_called()
        storage.upsert_event.assert_not_called()

    def test_regular_season_date_writes_scoreboard_and_upserts_events(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = True  # skip box-score fetch, isolate this test to the event upsert
        events = [_event(event_id="1"), _event(event_id="2")]
        client.get_scoreboard_for_date.return_value = {"events": events}

        with patch.object(backfill.normalize, "scoreboard_event_to_event_item", side_effect=lambda e: {"event_id": e["id"]}):
            result = backfill.process_date(client, storage, "20260115")

        assert result == _date_result(processed=2)
        assert storage.upsert_event.call_count == 2
        storage.put_raw_json.assert_any_call("nhl/scoreboard/20260115.json", {"events": events})

    def test_box_score_is_stored_under_each_events_own_season(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = False
        client.get_scoreboard_for_date.return_value = {"events": [_event(event_id="77", season_year=2020)]}
        client.get_summary.return_value = {"header": {}, "boxscore": {}}

        with patch.object(backfill.normalize, "scoreboard_event_to_event_item", return_value={}), \
             patch.object(backfill.normalize, "boxscore_to_player_game_stats", return_value=([], [])), \
             patch.object(backfill.normalize, "boxscore_to_team_game_stats", return_value=[]):
            backfill.process_date(client, storage, "20200815")

        storage.put_raw_json.assert_any_call("nhl/boxscore/2020/77.json", client.get_summary.return_value)

    def test_incomplete_event_is_not_fetched_as_a_box_score(self):
        client = MagicMock()
        storage = MagicMock()
        client.get_scoreboard_for_date.return_value = {"events": [_event(completed=False)]}

        with patch.object(backfill.normalize, "scoreboard_event_to_event_item", return_value={}):
            result = backfill.process_date(client, storage, "20260115")

        assert result["games_processed"] == 1
        client.get_summary.assert_not_called()

    def test_one_event_failure_does_not_block_the_others(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = True
        client.get_scoreboard_for_date.return_value = {"events": [_event(event_id="1"), _event(event_id="2")]}
        storage.upsert_event.side_effect = [Exception("write failed"), None]

        with patch.object(backfill.normalize, "scoreboard_event_to_event_item", side_effect=lambda e: {"event_id": e["id"]}):
            result = backfill.process_date(client, storage, "20260115")

        assert (result["games_processed"], result["games_failed"]) == (1, 1)
        assert result["failures"] == [{"date": "20260115", "event_id": "1", "error": "write failed"}]


class TestProcessGame:
    def test_skips_fetch_when_box_score_already_exists(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = True

        backfill.process_game(client, storage, 2026, "401803584")

        client.get_summary.assert_not_called()

    def test_fetches_writes_and_normalizes_when_missing(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = False
        entities = [{"entity_id": "p1"}, {"entity_id": "p2"}]

        with patch.object(backfill.normalize, "boxscore_to_player_game_stats", return_value=([{"row": 1}], entities)), \
             patch.object(backfill.normalize, "boxscore_to_team_game_stats", return_value=[{"team": 1}]):
            backfill.process_game(client, storage, 2026, "401803584")

        storage.put_raw_json.assert_called_once_with("nhl/boxscore/2026/401803584.json", client.get_summary.return_value)
        assert [c.args[0] for c in storage.upsert_player_entity.call_args_list] == entities
        storage.write_player_game_stats.assert_called_once_with([{"row": 1}])
        storage.write_team_game_stats.assert_called_once_with([{"team": 1}])


class TestProcessSeason:
    def test_sums_results_across_every_date_and_formats_dates_as_yyyymmdd(self):
        client = MagicMock()
        storage = MagicMock()
        results = [_date_result(processed=1, skipped=2), _date_result(processed=2, failed=1, failures=[{"event_id": "1"}])]

        with patch.object(backfill, "season_date_range", return_value=[date(2025, 10, 7), date(2025, 10, 8)]), \
             patch.object(backfill, "process_date", side_effect=results) as mock_process_date:
            result = backfill.process_season(client, storage, 2026)

        assert result == {
            "season": 2026, "games_processed": 3, "games_failed": 1, "games_skipped": 2, "failures": [{"event_id": "1"}],
        }
        assert [c.args[2] for c in mock_process_date.call_args_list] == ["20251007", "20251008"]


class TestProcessBatch:
    def test_processes_each_season_in_order(self):
        results = [{"season": s, "games_processed": 1, "games_failed": 0, "games_skipped": 0, "failures": []} for s in (2025, 2026)]
        with patch.object(backfill, "process_season", side_effect=results) as mock_process_season:
            assert backfill.process_batch("client", "storage", [2025, 2026]) == results

        assert [c.args for c in mock_process_season.call_args_list] == [("client", "storage", 2025), ("client", "storage", 2026)]


class TestMain:
    def _fake_result(self, season, processed=1, failed=0, failures=None):
        return {"season": season, "games_processed": processed, "games_failed": failed, "games_skipped": 0, "failures": failures or []}

    def test_seeds_teams_and_delegates_batches_to_process_batch(self, monkeypatch):
        monkeypatch.setenv("START_SEASON", "2025")
        monkeypatch.setenv("END_SEASON", "2026")
        monkeypatch.setenv("BATCH_SIZE", "2")
        monkeypatch.setattr("sys.argv", ["backfill.py"])
        mock_storage = MagicMock()

        with patch.object(backfill, "NHLClient"), \
             patch.object(backfill, "PipelineStorage", return_value=mock_storage), \
             patch.object(backfill, "seed_teams") as mock_seed_teams, \
             patch.object(backfill, "process_batch", return_value=[self._fake_result(2025), self._fake_result(2026)]) as mock_process_batch:
            backfill.main()

        mock_seed_teams.assert_called_once()
        mock_process_batch.assert_called_once_with(mock_seed_teams.call_args.args[0], mock_storage, [2025, 2026])
        mock_storage.put_raw_json.assert_not_called()

    def test_failures_are_written_to_s3_and_exit_code_is_1(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["backfill.py", "--start-season", "2026", "--end-season", "2026"])
        mock_storage = MagicMock()
        failure = {"date": "20260115", "event_id": "1", "error": "boom"}

        with patch.object(backfill, "NHLClient"), \
             patch.object(backfill, "PipelineStorage", return_value=mock_storage), \
             patch.object(backfill, "seed_teams"), \
             patch.object(backfill, "process_batch", return_value=[self._fake_result(2026, failed=1, failures=[failure])]):
            with pytest.raises(SystemExit) as exc_info:
                backfill.main()

        assert exc_info.value.code == 1
        assert mock_storage.put_raw_json.call_args.args[0].startswith("nhl/backfill-failures/")
        assert mock_storage.put_raw_json.call_args.args[1] == {"failures": [failure]}

    def test_a_batch_raising_does_not_stop_the_others_from_being_reported(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["backfill.py", "--start-season", "2025", "--end-season", "2026", "--batch-size", "1"])
        mock_storage = MagicMock()

        with patch.object(backfill, "NHLClient"), \
             patch.object(backfill, "PipelineStorage", return_value=mock_storage), \
             patch.object(backfill, "seed_teams"), \
             patch.object(backfill, "process_batch", side_effect=[Exception("batch died"), [self._fake_result(2026)]]):
            backfill.main()  # should not raise despite one batch failing

        mock_storage.put_raw_json.assert_not_called()
