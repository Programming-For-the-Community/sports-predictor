"""
Unit tests for data-backfills/pga/backfill.py's own logic -- everything
here is mocked, no real ESPN calls. Covers the season-batching, the
one-call calendar discovery (June 1 of the season's label year), the
idempotent per-tournament skip, the non-stroke-play-tournament skip
(Ryder Cup/Presidents Cup/WGC Match Play/Zurich Classic -- see
library.normalize.pga.is_medal_scoring's own docstring for the confirmed-
live crash this guards against), and per-tournament failure isolation.

The backfill module is importable directly (conftest.py inserts its
directory onto sys.path).
"""
from unittest.mock import MagicMock, patch

import backfill


def _calendar_entry(event_id="1", label="Some Championship"):
    return {"id": event_id, "label": label}


def _scoreboard(calendar):
    return {"leagues": [{"calendar": calendar}]}


def _leaderboard(event_id="1", scoring_system="Medal", tournament_name="Some Championship"):
    """Defaults to a real Medal (stroke-play) tournament shape -- callers
    testing the non-Medal skip path pass scoring_system="Match" (Ryder
    Cup/Presidents Cup/WGC Match Play) or "Teamstroke" (Zurich Classic)
    instead."""
    return {"events": [{
        "id": event_id,
        "tournament": {"displayName": tournament_name, "scoringSystem": {"name": scoring_system}},
    }]}


class TestChunkSeasons:
    def test_splits_into_batches_of_the_given_size(self):
        assert backfill.chunk_seasons(2017, 2022, 3) == [[2017, 2018, 2019], [2020, 2021, 2022]]

    def test_uneven_final_batch_is_shorter(self):
        assert backfill.chunk_seasons(2017, 2020, 3) == [[2017, 2018, 2019], [2020]]

    def test_single_season_range(self):
        assert backfill.chunk_seasons(2026, 2026, 3) == [[2026]]


class TestSeasonCalendar:
    def test_queries_june_1_of_the_season_label_year(self):
        client = MagicMock()
        client.get_scoreboard_for_date.return_value = _scoreboard([])

        backfill.season_calendar(client, 2026)

        client.get_scoreboard_for_date.assert_called_once_with("20260601")

    def test_returns_the_calendar_list_and_the_raw_scoreboard(self):
        client = MagicMock()
        calendar = [_calendar_entry("1"), _calendar_entry("2")]
        scoreboard = _scoreboard(calendar)
        client.get_scoreboard_for_date.return_value = scoreboard

        returned_calendar, returned_scoreboard = backfill.season_calendar(client, 2026)

        assert returned_calendar == calendar
        assert returned_scoreboard == scoreboard

    def test_missing_calendar_returns_an_empty_list_not_an_error(self):
        client = MagicMock()
        client.get_scoreboard_for_date.return_value = {"leagues": [{}]}

        calendar, _ = backfill.season_calendar(client, 2026)

        assert calendar == []


class TestProcessTournament:
    def test_skips_fetch_when_leaderboard_already_exists(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = True

        result = backfill.process_tournament(client, storage, 2026, "401811963")

        client.get_leaderboard.assert_not_called()
        assert result == "skipped"

    def test_fetches_writes_and_upserts_when_missing(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = False
        client.get_leaderboard.return_value = _leaderboard("401811963")

        with patch.object(backfill.normalize, "leaderboard_event_to_player_entities", return_value=[{"entity_id": "1"}, {"entity_id": "2"}]), \
             patch.object(backfill.normalize, "leaderboard_event_to_event_item", return_value={"event_id": "401811963"}):
            result = backfill.process_tournament(client, storage, 2026, "401811963")

        storage.put_raw_json.assert_called_once_with("pga/leaderboard/2026/401811963.json", client.get_leaderboard.return_value)
        assert storage.upsert_entity.call_count == 2
        storage.upsert_event.assert_called_once_with({"event_id": "401811963"})
        assert result == "processed"

    def test_no_events_in_response_is_skipped_without_raising(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = False
        client.get_leaderboard.return_value = {"events": []}

        result = backfill.process_tournament(client, storage, 2026, "401811963")  # must not raise

        storage.upsert_event.assert_not_called()
        assert result == "skipped"

    def test_match_play_tournament_is_skipped_not_normalized(self):
        # Ryder Cup / Presidents Cup / WGC-Dell Technologies Match Play --
        # confirmed live to crash the normalizer if not filtered here
        # first (see library.normalize.pga.is_medal_scoring).
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = False
        client.get_leaderboard.return_value = _leaderboard("401219595", scoring_system="Match", tournament_name="Ryder Cup")

        result = backfill.process_tournament(client, storage, 2026, "401219595")

        storage.upsert_event.assert_not_called()
        storage.upsert_entity.assert_not_called()
        assert result == "skipped"
        # Raw JSON is still preserved even though it's not normalized.
        storage.put_raw_json.assert_called_once()

    def test_team_stroke_play_tournament_is_skipped_not_normalized(self):
        # Zurich Classic of New Orleans -- confirmed live to KeyError in
        # the normalizer (team competitors have no "athlete" key).
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = False
        client.get_leaderboard.return_value = _leaderboard("401353230", scoring_system="Teamstroke", tournament_name="Zurich Classic of New Orleans")

        result = backfill.process_tournament(client, storage, 2026, "401353230")

        storage.upsert_event.assert_not_called()
        assert result == "skipped"

    def test_missing_tournament_metadata_is_skipped_not_normalized(self):
        # A just-added future calendar entry ESPN hasn't fully populated
        # yet -- confirmed live (a real not-yet-configured Presidents Cup
        # entry, 2026-08-25). Fails closed rather than assume Medal.
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = False
        client.get_leaderboard.return_value = {"events": [{"id": "401824815"}]}  # no "tournament" key at all

        result = backfill.process_tournament(client, storage, 2026, "401824815")

        storage.upsert_event.assert_not_called()
        assert result == "skipped"


class TestProcessSeason:
    def test_writes_the_scoreboard_and_counts_skipped_entries(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = True  # isolate this test to the calendar walk
        calendar = [_calendar_entry("1"), _calendar_entry("2")]

        with patch.object(backfill, "season_calendar", return_value=(calendar, {"leagues": []})):
            result = backfill.process_season(client, storage, 2026)

        assert result["tournaments_processed"] == 0
        assert result["tournaments_skipped"] == 2
        assert result["tournaments_failed"] == 0
        storage.put_raw_json.assert_any_call("pga/scoreboard/20260601.json", {"leagues": []})

    def test_counts_a_real_processed_tournament_separately_from_a_skipped_one(self):
        client = MagicMock()
        storage = MagicMock()
        calendar = [_calendar_entry("1"), _calendar_entry("2")]

        with patch.object(backfill, "season_calendar", return_value=(calendar, {})), \
             patch.object(backfill, "process_tournament", side_effect=["processed", "skipped"]):
            result = backfill.process_season(client, storage, 2026)

        assert result["tournaments_processed"] == 1
        assert result["tournaments_skipped"] == 1
        assert result["tournaments_failed"] == 0

    def test_one_tournament_failure_does_not_block_the_others(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.side_effect = [False, RuntimeError("boom")]
        calendar = [_calendar_entry("1"), _calendar_entry("2")]

        with patch.object(backfill, "season_calendar", return_value=(calendar, {})), \
             patch.object(backfill, "process_tournament", side_effect=["processed", Exception("ESPN timeout")]):
            result = backfill.process_season(client, storage, 2026)

        assert result["tournaments_processed"] == 1
        assert result["tournaments_failed"] == 1
        assert len(result["failures"]) == 1
