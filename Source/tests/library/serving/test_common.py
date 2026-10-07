"""
Unit tests for library.serving.common -- enrich_participants (shared by
nfl_reads.py and ncaafb_reads.py's own list_events), enrich_team_standings
(shared by every sport's season_projection.py), enrich_bracket_team_names
(shared by every sport's own _bracket_payload, added 2026-08-16 for the
playoff-bracket feature), and list_models (added 2026-08-27 as the
canonical home for library.serving.pga_reads -- see that module's own
docstring for why the pre-existing per-sport nba_reads.py/etc. copies
weren't rewired to import from here). See their respective test files
for the through-list_events/through-build_season_projection integration.
"""
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from library.parsing import us_eastern_date
from library.serving import common
from library.storage import event_list_snapshot
from library.serving.common import (
    enrich_bracket_team_names, enrich_participants, enrich_team_standings, latest_matching_row, list_models,
    most_recent_event, prefetch_entities, row_model_version,
)


class TestLatestMatchingRow:
    def test_none_when_nothing_matches(self):
        rows = [{"model_key": "MODEL#score-margin#v1", "generated_at": "2026-01-01T00:00:00+00:00"}]
        assert latest_matching_row(rows, "win-probability") is None

    def test_single_match_returns_it(self):
        row = {"model_key": "MODEL#win-probability#v1", "generated_at": "2026-01-01T00:00:00+00:00"}
        assert latest_matching_row([row], "win-probability") is row

    def test_picks_the_most_recently_generated_row_regardless_of_version_or_list_order(self):
        # A model repromoted more than once before its event was finally
        # played leaves one row per version -- DynamoDB's default
        # ascending model_key sort ("v1" before "v10") is not generation
        # order, so this must go by generated_at, not by list position or
        # by parsing the version out of the key.
        stale_v1 = {"model_key": "MODEL#win-probability#v1", "generated_at": "2026-08-06T00:00:00+00:00"}
        latest_v10 = {"model_key": "MODEL#win-probability#v10", "generated_at": "2026-09-15T00:00:00+00:00"}
        mid_v4 = {"model_key": "MODEL#win-probability#v4", "generated_at": "2026-09-01T00:00:00+00:00"}

        assert latest_matching_row([latest_v10, stale_v1, mid_v4], "win-probability") is latest_v10
        assert latest_matching_row([stale_v1, mid_v4, latest_v10], "win-probability") is latest_v10

    def test_only_matches_the_exact_prefix_not_a_substring(self):
        # "score-margin" must not match a "score-margin-v2" style typo'd
        # or differently-named model sharing the same leading substring --
        # the trailing "#" in the match is what anchors this.
        rows = [{"model_key": "MODEL#score-margin-extra#v1", "generated_at": "2026-01-01T00:00:00+00:00"}]
        assert latest_matching_row(rows, "score-margin") is None


class TestRowModelVersion:
    @pytest.mark.parametrize("model_key, expected", [
        ("MODEL#win-probability#v4", 4),
        ("MODEL#score-margin#v12", 12),
        ("MODEL#player-prop-points#v3#PLAYER#1966", 3),
        ("MODEL#top-10-probability#v1#GOLFER#9478", 1),
    ])
    def test_reads_the_version_out_of_the_key(self, model_key, expected):
        assert row_model_version({"model_key": model_key}) == expected

    @pytest.mark.parametrize("row", [
        {"model_key": "MODEL#win-probability"},
        {"model_key": "MODEL#win-probability#vX"},
        {"model_key": "PREGAME#MODEL#win-probability#v4"},
        {},
    ])
    def test_none_when_the_key_has_no_version(self, row):
        assert row_model_version(row) is None


class TestEnrichParticipants:
    def test_none_passes_through_unchanged(self):
        assert enrich_participants(MagicMock(), "nfl", None) is None

    def test_empty_list_passes_through_unchanged(self):
        assert enrich_participants(MagicMock(), "nfl", []) == []

    def test_attaches_name_abbreviation_and_conference_from_the_entity(self):
        storage = MagicMock()
        storage.get_entity.return_value = {
            "name": "Alabama", "metadata": {"abbreviation": "ALA", "conference": "SEC"},
        }

        result = enrich_participants(storage, "ncaafb", [{"entity_id": "333", "role": "home"}])

        assert result[0]["name"] == "Alabama"
        assert result[0]["abbreviation"] == "ALA"
        assert result[0]["conference"] == "SEC"
        assert result[0]["entity_id"] == "333"
        assert result[0]["role"] == "home"
        storage.get_entity.assert_called_once_with("ncaafb", "333", "team")

    def test_missing_entity_degrades_to_none_fields_not_an_error(self):
        storage = MagicMock()
        storage.get_entity.return_value = None

        result = enrich_participants(storage, "ncaafb", [{"entity_id": "333", "role": "away"}])

        assert result[0]["name"] is None
        assert result[0]["abbreviation"] is None
        assert result[0]["conference"] is None

    def test_entity_with_no_metadata_degrades_to_none_abbreviation(self):
        storage = MagicMock()
        storage.get_entity.return_value = {"name": "Alabama"}

        result = enrich_participants(storage, "ncaafb", [{"entity_id": "333", "role": "home"}])

        assert result[0]["name"] == "Alabama"
        assert result[0]["abbreviation"] is None

    def test_each_participant_resolved_independently(self):
        storage = MagicMock()
        storage.get_entity.side_effect = lambda sport, entity_id, entity_type: {
            "12": {"name": "Chiefs", "metadata": {"abbreviation": "KC"}},
            "24": {"name": "Chargers", "metadata": {"abbreviation": "LAC"}},
        }[entity_id]

        result = enrich_participants(storage, "nfl", [
            {"entity_id": "12", "role": "home"}, {"entity_id": "24", "role": "away"},
        ])

        assert [p["abbreviation"] for p in result] == ["KC", "LAC"]

    def test_defaults_to_looking_up_a_team_entity(self):
        storage = MagicMock()
        storage.get_entity.return_value = {"name": "Chiefs", "metadata": {"abbreviation": "KC"}}

        enrich_participants(storage, "nfl", [{"entity_id": "12", "role": "home"}])

        storage.get_entity.assert_called_once_with("nfl", "12", "team")

    def test_field_event_sport_looks_up_a_player_entity_instead_of_a_team(self):
        storage = MagicMock()
        storage.get_entity.return_value = {"name": "Scottie Scheffler", "metadata": {}}

        result = enrich_participants(
            storage, "pga", [{"entity_id": "9478", "result": {"finish_position": 1}}], entity_type="player",
        )

        storage.get_entity.assert_called_once_with("pga", "9478", "player")
        assert result[0]["name"] == "Scottie Scheffler"
        assert result[0]["abbreviation"] is None

    def test_entity_cache_hit_skips_get_entity_entirely(self):
        storage = MagicMock()
        cache = {("9478", "player"): {"name": "Scottie Scheffler", "metadata": {}}}

        result = enrich_participants(
            storage, "pga", [{"entity_id": "9478"}], entity_type="player", entity_cache=cache,
        )

        assert result[0]["name"] == "Scottie Scheffler"
        storage.get_entity.assert_not_called()

    def test_entity_cache_miss_falls_back_to_get_entity(self):
        storage = MagicMock()
        storage.get_entity.return_value = {"name": "Rory McIlroy", "metadata": {}}
        cache: dict = {}  # prefetch didn't include this golfer

        result = enrich_participants(
            storage, "pga", [{"entity_id": "9999"}], entity_type="player", entity_cache=cache,
        )

        assert result[0]["name"] == "Rory McIlroy"
        storage.get_entity.assert_called_once_with("pga", "9999", "player")


class TestPrefetchEntities:
    def test_delegates_to_storages_own_get_entities(self):
        storage = MagicMock()
        storage.get_entities.return_value = {("9478", "player"): {"name": "Scottie Scheffler"}}

        result = prefetch_entities(storage, "pga", [("9478", "player")])

        assert result == {("9478", "player"): {"name": "Scottie Scheffler"}}
        storage.get_entities.assert_called_once_with("pga", [("9478", "player")])


class TestMostRecentEvent:
    def test_empty_list_returns_empty(self):
        assert most_recent_event([]) == []

    def test_returns_only_the_latest_dated_event(self):
        events = [
            {"event_key": "E1", "event_date": "2026-06-01"},
            {"event_key": "E2", "event_date": "2026-08-15"},
            {"event_key": "E3", "event_date": "2026-07-04"},
        ]

        result = most_recent_event(events)

        assert [e["event_key"] for e in result] == ["E2"]

    def test_single_event_returns_that_event(self):
        events = [{"event_key": "E1", "event_date": "2026-06-01"}]

        assert most_recent_event(events) == events


class TestEnrichTeamStandings:
    def test_attaches_name_and_abbreviation_from_the_entity(self):
        storage = MagicMock()
        storage.get_entity.return_value = {"name": "Alabama", "metadata": {"abbreviation": "ALA"}}

        result = enrich_team_standings(storage, "ncaafb", [{"team_id": "333", "wins": 5}])

        assert result[0]["name"] == "Alabama"
        assert result[0]["abbreviation"] == "ALA"
        assert result[0]["team_id"] == "333"
        assert result[0]["wins"] == 5
        storage.get_entity.assert_called_once_with("ncaafb", "333", "team")

    def test_missing_entity_degrades_to_none_fields_not_an_error(self):
        storage = MagicMock()
        storage.get_entity.return_value = None

        result = enrich_team_standings(storage, "ncaafb", [{"team_id": "333"}])

        assert result[0]["name"] is None
        assert result[0]["abbreviation"] is None

    def test_each_row_resolved_independently(self):
        storage = MagicMock()
        storage.get_entity.side_effect = lambda sport, team_id, entity_type: {
            "12": {"name": "Chiefs", "metadata": {"abbreviation": "KC"}},
            "24": {"name": "Chargers", "metadata": {"abbreviation": "LAC"}},
        }[team_id]

        result = enrich_team_standings(storage, "nfl", [{"team_id": "12"}, {"team_id": "24"}])

        assert [row["abbreviation"] for row in result] == ["KC", "LAC"]

    def test_empty_list_passes_through_unchanged(self):
        assert enrich_team_standings(MagicMock(), "nfl", []) == []


class TestEnrichBracketTeamNames:
    def _storage(self):
        storage = MagicMock()
        storage.get_entity.side_effect = lambda sport, team_id, entity_type: {
            "12": {"name": "Chiefs", "metadata": {"abbreviation": "KC"}},
            "24": {"name": "Chargers", "metadata": {"abbreviation": "LAC"}},
        }.get(team_id)
        return storage

    def test_collects_team_ids_from_conference_split_rounds(self):
        bracket = {
            "conferences": {
                "AFC": [{"round": "Wild Card", "matchups": [{"team_a": "12", "team_b": "24"}]}],
            },
        }

        result = enrich_bracket_team_names(self._storage(), "nfl", bracket)

        assert result["team_names"]["12"] == {"name": "Chiefs", "abbreviation": "KC", "color": None}
        assert result["team_names"]["24"] == {"name": "Chargers", "abbreviation": "LAC", "color": None}

    def test_collects_team_ids_from_a_flat_rounds_list(self):
        bracket = {"rounds": [{"round": "Round of 12", "matchups": [{"team_a": "12", "team_b": "24"}]}]}

        result = enrich_bracket_team_names(self._storage(), "ncaafb", bracket)

        assert set(result["team_names"]) == {"12", "24"}

    def test_collects_team_ids_from_the_final_matchup(self):
        bracket = {"conferences": {}, "super_bowl": {"team_a": "12", "team_b": "24"}}

        result = enrich_bracket_team_names(self._storage(), "nfl", bracket)

        assert set(result["team_names"]) == {"12", "24"}

    def test_a_team_id_appearing_in_multiple_matchups_is_only_looked_up_once(self):
        bracket = {
            "conferences": {
                "AFC": [
                    {"round": "Wild Card", "matchups": [{"team_a": "12", "team_b": "24"}]},
                    {"round": "Divisional", "matchups": [{"team_a": "12", "team_b": "99"}]},
                ],
            },
        }
        storage = self._storage()

        enrich_bracket_team_names(storage, "nfl", bracket)

        assert storage.get_entity.call_count == 3  # 12, 24, 99 -- not 4

    def test_missing_entity_degrades_to_none_fields_not_an_error(self):
        storage = MagicMock()
        storage.get_entity.return_value = None
        bracket = {"conferences": {}, "rounds": [{"round": "R1", "matchups": [{"team_a": "1", "team_b": "2"}]}]}

        result = enrich_bracket_team_names(storage, "ncaafb", bracket)

        assert result["team_names"]["1"] == {"name": None, "abbreviation": None, "color": None}

    def test_the_original_bracket_fields_are_preserved(self):
        bracket = {"conferences": {}, "champion": "12"}

        result = enrich_bracket_team_names(self._storage(), "nfl", bracket)

        assert result["champion"] == "12"

    def test_collects_team_ids_from_march_madness_regions_first_four_and_final_four(self):
        bracket = {
            "first_four": [{"team_a": "1", "team_b": "2"}],
            "regions": {
                "Region A": {"rounds": [{"round": "Round of 64", "matchups": [{"team_a": "12", "team_b": "24"}]}], "champion": "12"},
            },
            "final_four": [{"team_a": "12", "team_b": "99"}],
            "championship": {"team_a": "12", "team_b": "1"},
        }

        result = enrich_bracket_team_names(self._storage(), "ncaambb", bracket)

        assert set(result["team_names"]) == {"1", "2", "12", "24", "99"}


class TestListModels:
    def test_empty_when_no_models_promoted(self):
        s3 = MagicMock()
        s3.list_keys.return_value = []
        assert list_models(s3, "pga") == {"sport": "pga", "models": []}

    def test_lists_promoted_models_with_card_summaries(self):
        s3 = MagicMock()
        s3.list_keys.return_value = ["pga/top-10-probability/v1/model_card.json"]
        s3.object_exists.return_value = True
        s3.get_json.side_effect = [
            {"version": 1},
            {
                "model_name": "top-10-probability", "algorithm": "xgboost", "version": 1,
                "trained_at": "2026-01-01T00:00:00Z", "accuracy": 0.7,
                "feature_importances": {"avg_score_to_par": 0.3},
            },
        ]

        result = list_models(s3, "pga")

        assert len(result["models"]) == 1
        assert result["models"][0]["model_name"] == "top-10-probability"

    def test_model_never_promoted_is_excluded(self):
        s3 = MagicMock()
        s3.list_keys.return_value = ["pga/top-10-probability/v1/model_card.json"]
        s3.object_exists.return_value = False

        result = list_models(s3, "pga")

        assert result["models"] == []


class TestIsCurrentOrUpcoming:
    NOW = datetime(2026, 9, 27, 4, 3, tzinfo=timezone.utc)  # 12:03am ET

    def _event(self, event_date, kickoff_time):
        return {"event_date": event_date, "kickoff_time": kickoff_time}

    def test_dated_today_or_later_eastern_counts(self):
        assert common.is_current_or_upcoming(self._event("2026-09-27", "2026-09-27T16:00:00.000Z"), self.NOW)
        assert common.is_current_or_upcoming(self._event("2026-10-03", "2026-10-03T16:00:00.000Z"), self.NOW)

    def test_yesterdays_late_kickoff_still_in_progress_counts(self):
        # 10pm CT = 03:00 UTC, dated the day before in Eastern terms.
        assert common.is_current_or_upcoming(self._event("2026-09-26", "2026-09-27T03:00:00.000Z"), self.NOW)

    def test_yesterdays_game_past_the_live_window_does_not(self):
        assert not common.is_current_or_upcoming(self._event("2026-09-26", "2026-09-26T16:00:00.000Z"), self.NOW)

    def test_a_past_game_without_a_usable_kickoff_does_not(self):
        assert not common.is_current_or_upcoming({"event_date": "2026-09-26"}, self.NOW)
        assert not common.is_current_or_upcoming(self._event("2026-09-26", "not a time"), self.NOW)


class TestMalformedEventsAndRows:
    _ONE_SIDED = {"participants": [{"entity_id": "1", "role": "home"}]}

    def test_actual_result_is_none_without_both_sides(self):
        assert common._actual_result(self._ONE_SIDED) is None

    def test_basketball_leaders_comparison_is_none_without_both_sides(self):
        assert common._basketball_leaders_comparison(MagicMock(), [], "nba", self._ONE_SIDED) is None

    def test_predicted_stats_ignore_stats_with_no_leader_category(self):
        rows = [
            {"model_key": "MODEL#player-prop-points#v1#PLAYER#p1", "predicted_value": {"value": 20.0}},
            {"model_key": "MODEL#player-prop-blocks#v1#PLAYER#p1", "predicted_value": {"value": 2.0}},
            {"model_key": "MODEL#win-probability#v1", "predicted_value": {"home_win_probability": 0.5}},
        ]

        result = common._predicted_stats_by_entity_category(rows, {"points": "scoring"})

        assert result == {("p1", "scoring"): {"points": 20.0}}


def _prediction_bucket(objects: dict):
    bucket = MagicMock()
    bucket.get_json_or_none.side_effect = objects.get
    return bucket


def _core_pointers(sport: str, version: int) -> dict:
    return {
        f"{sport}/{model}/current.json": {"version": version}
        for model in ("win-probability", "score-margin", "home-score", "away-score")
    }


_CORE_VERSIONS = {"win_probability": 2, "margin": 2, "home_score": 2, "away_score": 2}


class TestCachedPredictionReader:
    """What a scheduled events list embeds per game in place of the
    client's own per-game prediction request."""

    KEY = "SPORT#NBA#EVENT#e1"
    CACHE_KEY = "predictions-cache/nba/events/SPORT#NBA#EVENT#e1.json"

    def _entry(self, **overrides):
        return {
            "model_versions": _CORE_VERSIONS, "event_status": "scheduled",
            "cached_at_epoch": time.time(), "result": {"predictions": {"margin": {"value": 3.5}}},
            **overrides,
        }

    def test_fresh_entry_is_served_as_not_stale(self):
        bucket = _prediction_bucket({**_core_pointers("nba", 2), self.CACHE_KEY: self._entry()})

        prediction = common.cached_prediction_reader(bucket, "nba")(self.KEY)

        assert prediction == {"predictions": {"margin": {"value": 3.5}}, "stale": False}

    def test_entry_from_a_superseded_model_is_served_stale(self):
        bucket = _prediction_bucket({**_core_pointers("nba", 3), self.CACHE_KEY: self._entry()})

        prediction = common.cached_prediction_reader(bucket, "nba")(self.KEY)

        assert prediction["stale"] is True
        assert prediction["retry_after_seconds"] == common.STALE_RETRY_AFTER_SECONDS

    def test_miss_is_none(self):
        bucket = _prediction_bucket(_core_pointers("nba", 2))

        assert common.cached_prediction_reader(bucket, "nba")(self.KEY) is None

    def test_negative_entry_is_none(self):
        error_entry = {"error_type": "EventNotFoundError", "error": "no such event", "cached_at_epoch": time.time()}
        bucket = _prediction_bucket({**_core_pointers("nba", 2), self.CACHE_KEY: error_entry})

        assert common.cached_prediction_reader(bucket, "nba")(self.KEY) is None

    def test_never_writes_or_claims(self):
        bucket = _prediction_bucket({**_core_pointers("nba", 3), self.CACHE_KEY: self._entry()})

        common.cached_prediction_reader(bucket, "nba")(self.KEY)

        bucket.put_json.assert_not_called()


class TestScheduledPredictionReader:
    def test_none_without_a_bucket(self):
        assert common.scheduled_prediction_reader(None, "nba", "scheduled") is None

    def test_none_for_a_completed_list(self):
        assert common.scheduled_prediction_reader(MagicMock(), "nba", "completed") is None

    def test_a_reader_for_a_scheduled_list_with_a_bucket(self):
        bucket = _prediction_bucket(_core_pointers("nba", 2))

        assert callable(common.scheduled_prediction_reader(bucket, "nba", "scheduled"))


class TestListEventsEmbedsPredictions:
    def _storage(self, event_date: str):
        storage = MagicMock()
        storage.get_all_events.return_value = [{
            "event_id": "e1", "event_key": "SPORT#NBA#EVENT#e1", "event_date": event_date, "status": "scheduled",
            "participants": [{"entity_id": "13", "role": "home"}, {"entity_id": "2", "role": "away"}],
        }]
        storage.get_entities.return_value = {}
        storage.get_entity.return_value = None
        return storage

    def test_scheduled_event_carries_its_cached_prediction(self):
        event_date = us_eastern_date(datetime.now(timezone.utc) + timedelta(days=2))
        bucket = _prediction_bucket({
            **_core_pointers("nba", 2),
            "predictions-cache/nba/events/SPORT#NBA#EVENT#e1.json": {
                "model_versions": _CORE_VERSIONS, "event_status": "scheduled",
                "cached_at_epoch": time.time(), "result": {"predictions": {}},
            },
        })

        result = common.list_events_grouped_by_day(self._storage(event_date), MagicMock(), "nba", "scheduled", model_bucket=bucket)

        assert result["events"][0]["prediction"] == {"predictions": {}, "stale": False}

    def test_uncached_event_carries_a_null_prediction(self):
        event_date = us_eastern_date(datetime.now(timezone.utc) + timedelta(days=2))
        bucket = _prediction_bucket(_core_pointers("nba", 2))

        result = common.list_events_grouped_by_day(self._storage(event_date), MagicMock(), "nba", "scheduled", model_bucket=bucket)

        assert result["events"][0]["prediction"] is None

    def test_no_prediction_key_without_a_bucket(self):
        event_date = us_eastern_date(datetime.now(timezone.utc) + timedelta(days=2))

        result = common.list_events_grouped_by_day(self._storage(event_date), MagicMock(), "nba", "scheduled")

        assert "prediction" not in result["events"][0]



class TestCompletedListSnapshot:
    """A completed list is rebuilt only when its events changed."""

    def _storage(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [{
            "event_id": "e1", "event_key": "SPORT#NBA#EVENT#e1", "event_date": "2026-01-10", "status": "completed",
            "participants": [
                {"entity_id": "13", "role": "home", "result": {"score": 101}},
                {"entity_id": "2", "role": "away", "result": {"score": 99}},
            ],
        }]
        storage.get_entities.return_value = {}
        storage.get_entity.return_value = None
        storage.get_player_game_stats_for_event.return_value = []
        return storage

    def _predictions_table(self):
        table = MagicMock()
        table.query.return_value = []
        return table

    def test_a_matching_snapshot_is_served_without_rebuilding(self):
        storage, table = self._storage(), self._predictions_table()
        events_fingerprint = event_list_snapshot.fingerprint(storage.get_all_events.return_value)
        bucket = _prediction_bucket({
            "predictions-cache/lists/nba/completed.json": {
                "fingerprint": events_fingerprint, "built_at_epoch": time.time(), "entries": [{"event_id": "from-snapshot"}],
            },
        })

        result = common.list_events_grouped_by_day(storage, table, "nba", "completed", model_bucket=bucket)

        assert result == {"sport": "nba", "events": [{"event_id": "from-snapshot"}]}
        table.query.assert_not_called()
        storage.get_entities.assert_not_called()
        bucket.put_json.assert_not_called()

    def test_a_miss_builds_the_list_and_saves_it(self):
        storage, table = self._storage(), self._predictions_table()
        bucket = _prediction_bucket({})

        result = common.list_events_grouped_by_day(storage, table, "nba", "completed", model_bucket=bucket)

        assert result["events"][0]["event_id"] == "e1"
        key, payload = bucket.put_json.call_args.args
        assert key == "predictions-cache/lists/nba/completed.json"
        assert payload["entries"] == result["events"]
        assert payload["fingerprint"] == event_list_snapshot.fingerprint(storage.get_all_events.return_value)

    def test_a_snapshot_of_different_events_is_rebuilt(self):
        storage, table = self._storage(), self._predictions_table()
        bucket = _prediction_bucket({
            "predictions-cache/lists/nba/completed.json": {
                "fingerprint": "something-else", "built_at_epoch": time.time(), "entries": [{"event_id": "old"}],
            },
        })

        result = common.list_events_grouped_by_day(storage, table, "nba", "completed", model_bucket=bucket)

        assert result["events"][0]["event_id"] == "e1"

    def test_a_scheduled_list_never_touches_the_snapshot(self):
        assert common.read_completed_snapshot(MagicMock(), "nba", "scheduled", []) == (None, None)

    def test_no_snapshot_without_a_bucket(self):
        storage, table = self._storage(), self._predictions_table()

        result = common.list_events_grouped_by_day(storage, table, "nba", "completed")

        assert result["events"][0]["event_id"] == "e1"

