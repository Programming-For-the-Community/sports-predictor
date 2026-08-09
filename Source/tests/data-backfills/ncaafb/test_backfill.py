"""
Unit tests for data-backfills/ncaafb/backfill.py. Unlike NFL's own
backfill test suite (which hits ESPN's real, keyless API), everything
here is mocked -- see .github/workflows/ncaafb_backfill.yml's own
comment for why a CFBD-key-requiring, quota-capped API isn't a good fit
for a live-hitting CI suite. Hand-built synthetic payloads, same
discipline as the rest of the ncaafb test suite.

The backfill module is importable directly (conftest.py inserts its
directory onto sys.path, same as its sibling normalize.py).
"""
from unittest.mock import MagicMock, patch

import backfill


def _team(team_id="2", school="Georgia", dome=False):
    return {"id": team_id, "school": school, "conference": "SEC", "location": {"dome": dome}}


def _game(game_id="1", home_id="2", away_id="52", completed=False, season=2025, season_type="regular", week=4,
          start_date="2025-09-28T20:25:00.000Z", home_points=None, away_points=None):
    return {
        "id": game_id, "homeId": home_id, "awayId": away_id, "completed": completed,
        "season": season, "seasonType": season_type, "week": week, "startDate": start_date,
        "homePoints": home_points, "awayPoints": away_points,
        "homeConference": "SEC", "awayConference": "SEC", "conferenceGame": True,
    }


class TestChunkSeasons:
    def test_splits_into_batches_of_the_given_size(self):
        assert backfill.chunk_seasons(2015, 2020, 2) == [[2015, 2016], [2017, 2018], [2019, 2020]]

    def test_uneven_final_batch_is_shorter(self):
        assert backfill.chunk_seasons(2015, 2019, 2) == [[2015, 2016], [2017, 2018], [2019]]

    def test_single_season_range(self):
        assert backfill.chunk_seasons(2025, 2025, 2) == [[2025]]


class TestSeedTeams:
    def test_upserts_a_team_entity_per_team(self):
        client = MagicMock()
        storage = MagicMock()
        s3 = MagicMock()

        with patch.object(backfill, "get_cached_teams", return_value=[_team("2", "Georgia"), _team("52", "Alabama")]):
            backfill.seed_teams(client, storage, s3, "bucket", 2025)

        assert storage.upsert_entity.call_count == 2
        entity_ids = {call.args[0]["entity_id"] for call in storage.upsert_entity.call_args_list}
        assert entity_ids == {"2", "52"}

    def test_fetches_teams_for_the_given_season(self):
        client = MagicMock()
        storage = MagicMock()
        s3 = MagicMock()

        with patch.object(backfill, "get_cached_teams", return_value=[]) as mock_get:
            backfill.seed_teams(client, storage, s3, "bucket", 2025)

        mock_get.assert_called_once_with(s3, "bucket", client, 2025)


class TestSeasonRankLookup:
    def test_writes_the_raw_payload_to_s3(self):
        client = MagicMock()
        storage = MagicMock()
        client.get_rankings.return_value = [
            {"week": 4, "polls": [{"poll": "AP Top 25", "ranks": [{"school": "Georgia", "rank": 1}]}]},
        ]

        backfill._season_rank_lookup(client, storage, 2025)

        storage.put_raw_json.assert_called_once_with("ncaafb/rankings/2025.json", client.get_rankings.return_value)

    def test_builds_a_per_week_school_to_rank_lookup(self):
        client = MagicMock()
        storage = MagicMock()
        client.get_rankings.return_value = [
            {"week": 4, "polls": [{"poll": "AP Top 25", "ranks": [{"school": "Georgia", "rank": 1}]}]},
            {"week": 5, "polls": [{"poll": "AP Top 25", "ranks": [{"school": "Georgia", "rank": 2}]}]},
        ]

        result = backfill._season_rank_lookup(client, storage, 2025)

        assert result == {4: {"Georgia": 1}, 5: {"Georgia": 2}}

    def test_week_entries_missing_a_week_number_are_skipped(self):
        client = MagicMock()
        storage = MagicMock()
        client.get_rankings.return_value = [{"polls": []}]

        result = backfill._season_rank_lookup(client, storage, 2025)

        assert result == {}


class TestAttachEnrichment:
    def _by_id(self):
        return {
            "2": {"school": "Georgia", "location": {"dome": False}},
            "52": {"school": "Alabama", "location": {"dome": True}},
        }

    def test_attaches_venue_indoor_from_home_team(self):
        games = [_game(home_id="52")]
        backfill._attach_enrichment(games, self._by_id(), {}, {})
        assert games[0]["venue_indoor"] is True

    def test_attaches_coach_and_rank_by_resolved_school(self):
        games = [_game(home_id="2", away_id="52")]
        coach_by_school = {"Georgia": {"coach_name": "Kirby Smart"}}
        rank_by_school = {"Georgia": 1}

        backfill._attach_enrichment(games, self._by_id(), coach_by_school, rank_by_school)

        assert games[0]["home_coach"]["coach_name"] == "Kirby Smart"
        assert games[0]["home_current_rank"] == 1
        assert games[0]["away_coach"] is None
        assert games[0]["away_current_rank"] is None

    def test_unresolvable_home_team_leaves_fields_none(self):
        games = [_game(home_id="999")]
        backfill._attach_enrichment(games, self._by_id(), {}, {})
        assert games[0]["venue_indoor"] is None
        assert games[0]["home_coach"] is None
        assert games[0]["home_current_rank"] is None


class TestAnnotateBoxScores:
    def test_injects_home_away_id_and_event_date(self):
        games_by_id = {"1": _game(game_id="1", home_id="2", away_id="52", start_date="2025-09-28T20:25:00.000Z")}
        box_scores = [{"id": "1"}]

        backfill._annotate_box_scores(box_scores, games_by_id)

        assert box_scores[0]["home_id"] == "2"
        assert box_scores[0]["away_id"] == "52"
        assert box_scores[0]["event_date"] == "2025-09-28"

    def test_entry_with_no_matching_game_is_left_untouched(self):
        box_scores = [{"id": "999"}]
        backfill._annotate_box_scores(box_scores, {})
        assert "home_id" not in box_scores[0]


class TestProcessWeek:
    def _kwargs(self, **overrides):
        base = dict(by_id={}, coach_by_school={}, rank_by_week={})
        base.update(overrides)
        return base

    def test_no_games_returns_zeroed_result_and_writes_nothing(self):
        client = MagicMock()
        client.get_games.return_value = []
        storage = MagicMock()

        result = backfill.process_week(client, storage, 2025, "regular", 4, **self._kwargs())

        assert result == {"games_processed": 0, "games_failed": 0, "failures": []}
        storage.put_raw_json.assert_not_called()
        storage.upsert_event.assert_not_called()

    def test_writes_the_raw_games_payload_and_upserts_each_event(self):
        client = MagicMock()
        client.get_games.return_value = [_game(game_id="1"), _game(game_id="2")]
        storage = MagicMock()

        result = backfill.process_week(client, storage, 2025, "regular", 4, **self._kwargs())

        assert result["games_processed"] == 2
        storage.put_raw_json.assert_any_call("ncaafb/games/2025/regular/4.json", client.get_games.return_value)
        assert storage.upsert_event.call_count == 2

    def test_no_completed_games_skips_box_score_fetch(self):
        client = MagicMock()
        client.get_games.return_value = [_game(completed=False)]
        storage = MagicMock()

        backfill.process_week(client, storage, 2025, "regular", 4, **self._kwargs())

        client.get_game_player_stats.assert_not_called()
        client.get_game_team_stats.assert_not_called()

    def test_completed_games_fetch_and_write_both_box_score_kinds(self):
        client = MagicMock()
        client.get_games.return_value = [_game(game_id="1", home_id="2", away_id="52", completed=True,
                                                 home_points=31, away_points=17)]
        client.get_game_player_stats.return_value = [{
            "id": "1",
            "teams": [{"homeAway": "home", "categories": [{"name": "passing", "types": [
                {"name": "YDS", "athletes": [{"id": "9001", "name": "QB One", "stat": "250"}]},
            ]}]}],
        }]
        client.get_game_team_stats.return_value = [{
            "id": "1",
            "teams": [{"teamId": "2", "homeAway": "home", "stats": [{"category": "totalYards", "stat": "400"}]}],
        }]
        storage = MagicMock()

        result = backfill.process_week(client, storage, 2025, "regular", 4, **self._kwargs())

        assert result["games_processed"] == 1
        assert result["games_failed"] == 0
        storage.write_player_game_stats.assert_called_once()
        storage.write_team_game_stats.assert_called_once()
        storage.upsert_player_entity.assert_called_once()

    def test_player_box_score_failure_is_recorded_and_does_not_block_team_box_scores(self):
        client = MagicMock()
        client.get_games.return_value = [_game(completed=True)]
        client.get_game_player_stats.side_effect = Exception("CFBD timeout")
        client.get_game_team_stats.return_value = []
        storage = MagicMock()

        result = backfill.process_week(client, storage, 2025, "regular", 4, **self._kwargs())

        assert result["games_failed"] == 1
        assert result["failures"][0]["week"] == 4
        client.get_game_team_stats.assert_called_once()

    def test_event_write_failure_for_one_game_does_not_block_others(self):
        client = MagicMock()
        client.get_games.return_value = [_game(game_id="1"), _game(game_id="2")]
        storage = MagicMock()
        storage.upsert_event.side_effect = [Exception("DynamoDB throttled"), None]

        result = backfill.process_week(client, storage, 2025, "regular", 4, **self._kwargs())

        assert result["games_processed"] == 1
        assert result["games_failed"] == 1
        assert result["failures"][0]["event_id"] == "1"


class TestProcessSeason:
    def test_aggregates_results_across_every_week(self):
        client = MagicMock()
        storage = MagicMock()
        canned = {"games_processed": 3, "games_failed": 1, "failures": [{"season": 2025, "event_id": "1", "error": "x"}]}

        with patch.object(backfill, "get_cached_teams", return_value=[]), \
             patch.object(backfill, "get_cached_coaches", return_value=[]), \
             patch.object(backfill, "process_week", return_value=canned) as mock_process_week:
            client.get_rankings.return_value = []
            result = backfill.process_season(client, storage, MagicMock(), "bucket", 2025)

        expected_weeks = len(backfill.REGULAR_SEASON_WEEKS) + len(backfill.POSTSEASON_WEEKS)
        assert mock_process_week.call_count == expected_weeks
        assert result["games_processed"] == 3 * expected_weeks
        assert result["games_failed"] == 1 * expected_weeks
        assert len(result["failures"]) == expected_weeks

    def test_teams_fetch_failure_does_not_raise_and_passes_empty_by_id(self):
        client = MagicMock()
        storage = MagicMock()
        client.get_rankings.return_value = []

        with patch.object(backfill, "get_cached_teams", side_effect=Exception("CFBD timeout")), \
             patch.object(backfill, "get_cached_coaches", return_value=[]), \
             patch.object(backfill, "process_week", return_value={"games_processed": 0, "games_failed": 0, "failures": []}) as mock_process_week:
            backfill.process_season(client, storage, MagicMock(), "bucket", 2025)

        assert mock_process_week.call_args.args[5] == {}  # by_id

    def test_coaches_fetch_failure_does_not_raise_and_passes_empty_lookup(self):
        client = MagicMock()
        storage = MagicMock()
        client.get_rankings.return_value = []

        with patch.object(backfill, "get_cached_teams", return_value=[]), \
             patch.object(backfill, "get_cached_coaches", side_effect=Exception("CFBD timeout")), \
             patch.object(backfill, "process_week", return_value={"games_processed": 0, "games_failed": 0, "failures": []}) as mock_process_week:
            backfill.process_season(client, storage, MagicMock(), "bucket", 2025)

        assert mock_process_week.call_args.args[6] == {}  # coach_by_school

    def test_rankings_fetch_failure_does_not_raise_and_passes_empty_lookup(self):
        client = MagicMock()
        storage = MagicMock()
        client.get_rankings.side_effect = Exception("CFBD timeout")

        with patch.object(backfill, "get_cached_teams", return_value=[]), \
             patch.object(backfill, "get_cached_coaches", return_value=[]), \
             patch.object(backfill, "process_week", return_value={"games_processed": 0, "games_failed": 0, "failures": []}) as mock_process_week:
            backfill.process_season(client, storage, MagicMock(), "bucket", 2025)

        assert mock_process_week.call_args.args[7] == {}  # rank_by_week


class TestProcessBatch:
    def test_processes_every_season_in_the_batch(self):
        client = MagicMock()
        storage = MagicMock()
        canned = {"season": None, "games_processed": 0, "games_failed": 0, "failures": []}

        with patch.object(backfill, "process_season", side_effect=lambda c, s, s3, b, season: {**canned, "season": season}) as mock_process_season:
            results = backfill.process_batch(client, storage, MagicMock(), "bucket", [2024, 2025])

        assert [r["season"] for r in results] == [2024, 2025]
        assert mock_process_season.call_count == 2
