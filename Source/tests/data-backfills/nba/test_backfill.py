"""
Unit tests for data-backfills/nba/backfill.py's own logic -- everything
here is mocked, no real ESPN calls (those live in test_espn_nba.py's own
integration suite). Covers the genuinely-new-vs-NFL pieces: the
date-based (not week-based) walk, the ending-year season convention, and
the preseason/incomplete-game skip branches in process_date.

The backfill module is importable directly (conftest.py inserts its
directory onto sys.path, same as its sibling normalize.py).
"""
from datetime import date
from unittest.mock import MagicMock, patch

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
