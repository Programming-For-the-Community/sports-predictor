"""
Unit tests for data-backfills/f1/backfill.py's own logic -- everything
here is mocked, no real Jolpica calls. Covers season-batching, the
one-call-per-season schedule discovery, the idempotent per-round-file
skip, the not-yet-run-round skip, the qualifying merge (fetched before
the event is built), real Sprint-race normalization, pitstops raw-only
caching, and per-round failure isolation.

The backfill module is importable directly (conftest.py inserts its
directory onto sys.path).
"""
from unittest.mock import MagicMock, patch

import backfill


def _schedule(rounds_and_dates):
    return {"MRData": {"RaceTable": {"Races": [{"round": str(r), "date": d} for r, d in rounds_and_dates]}}}


def _results(round_="1", has_results=True):
    results = [{
        "position": "1", "positionText": "1", "points": "25",
        "Driver": {"driverId": "max_verstappen", "givenName": "Max", "familyName": "Verstappen"},
        "Constructor": {"constructorId": "red_bull", "name": "Red Bull"},
        "grid": "1", "laps": "57", "status": "Finished",
    }] if has_results else []
    return {
        "MRData": {"RaceTable": {"season": "2024", "round": round_, "Races": [{
            "season": "2024", "round": round_, "raceName": "Test GP",
            "Circuit": {"circuitId": "test", "circuitName": "Test Circuit", "Location": {"locality": "Testville", "country": "Testland"}},
            "date": "2024-03-02", "Results": results,
        }]}},
    }


def _sprint_results(round_="5", has_results=True):
    results = [{
        "position": "1", "positionText": "1", "points": "8",
        "Driver": {"driverId": "max_verstappen", "givenName": "Max", "familyName": "Verstappen"},
        "Constructor": {"constructorId": "red_bull", "name": "Red Bull"},
        "grid": "1", "laps": "19", "status": "Finished",
    }] if has_results else []
    return {
        "MRData": {"RaceTable": {"season": "2024", "round": round_, "Races": [{
            "season": "2024", "round": round_, "raceName": "Test GP",
            "Circuit": {"circuitId": "test", "circuitName": "Test Circuit", "Location": {"locality": "Testville", "country": "Testland"}},
            "date": "2024-03-02", "SprintResults": results,
        }]}},
    }


def _qualifying_results(round_="1"):
    return {
        "MRData": {"RaceTable": {"season": "2024", "round": round_, "Races": [{
            "season": "2024", "round": round_,
            "QualifyingResults": [{
                "position": "1",
                "Driver": {"driverId": "max_verstappen"}, "Constructor": {"constructorId": "red_bull"},
                "Q1": "1:30.031", "Q2": "1:29.374", "Q3": "1:29.179",
            }],
        }]}},
    }


def _empty_endpoint():
    return {"MRData": {"RaceTable": {"Races": []}}}


def _storage(cached_keys=None):
    storage = MagicMock()
    cached = set(cached_keys or [])
    storage.raw_object_exists.side_effect = lambda key: key in cached
    return storage


class TestChunkSeasons:
    def test_splits_into_batches_of_the_given_size(self):
        assert backfill.chunk_seasons(2010, 2015, 3) == [[2010, 2011, 2012], [2013, 2014, 2015]]

    def test_uneven_final_batch_is_shorter(self):
        assert backfill.chunk_seasons(2010, 2014, 3) == [[2010, 2011, 2012], [2013, 2014]]

    def test_single_season_range(self):
        assert backfill.chunk_seasons(2024, 2024, 3) == [[2024]]


class TestProcessRound:
    def test_fetches_writes_and_upserts_when_missing(self):
        client = MagicMock()
        client.get_race_results.return_value = _results()
        client.get_qualifying.return_value = _empty_endpoint()
        client.get_pitstops.return_value = _empty_endpoint()
        client.get_sprint.return_value = _empty_endpoint()
        storage = _storage()

        result = backfill.process_round(client, storage, 2024, 1)

        assert result == "processed"
        storage.put_raw_json.assert_any_call("f1/results/2024/1.json", client.get_race_results.return_value)
        assert storage.upsert_event.call_count == 1
        assert storage.upsert_entity.call_count == 2  # 1 driver + 1 constructor

    def test_skips_the_jolpica_fetch_but_still_processes_when_results_already_cached(self):
        client = MagicMock()
        storage = _storage(cached_keys=["f1/results/2024/1.json"])
        storage.get_raw_json.return_value = _results()
        client.get_qualifying.return_value = _empty_endpoint()
        client.get_pitstops.return_value = _empty_endpoint()
        client.get_sprint.return_value = _empty_endpoint()

        result = backfill.process_round(client, storage, 2024, 1)

        assert result == "processed"
        client.get_race_results.assert_not_called()
        storage.get_raw_json.assert_called_once_with("f1/results/2024/1.json")
        assert storage.upsert_event.call_count == 1

    def test_a_round_with_no_results_yet_is_skipped_not_an_error(self):
        client = MagicMock()
        client.get_race_results.return_value = _results(has_results=False)
        storage = _storage()

        result = backfill.process_round(client, storage, 2024, 25)

        assert result == "skipped"
        storage.upsert_event.assert_not_called()
        # No supplemental fetches for a round that hasn't happened.
        client.get_qualifying.assert_not_called()

    def test_a_not_yet_run_round_is_never_cached_to_s3(self):
        # Regression: caching an empty "not run yet" response under the
        # same raw_key a real result would eventually occupy used to
        # permanently freeze this round as "skipped" forever, even after
        # Jolpica actually published real results -- a later re-run's own
        # storage.raw_object_exists check would find and trust the stale
        # empty file instead of re-querying Jolpica.
        client = MagicMock()
        client.get_race_results.return_value = _results(has_results=False)
        storage = _storage()

        backfill.process_round(client, storage, 2024, 25)

        storage.put_raw_json.assert_not_called()

    def test_a_round_that_later_becomes_available_is_correctly_re_fetched_and_processed(self):
        # Simulates the exact regression scenario: run 1 sees no results
        # yet (correctly not cached per the test above); run 2, against
        # the SAME storage (still reporting nothing cached, matching
        # reality since nothing was ever written), now finds real
        # results and must process them, not stay stuck on "skipped".
        client = MagicMock()
        storage = _storage()  # raw_object_exists always False -- nothing was ever cached

        client.get_race_results.return_value = _results(has_results=False)
        first_result = backfill.process_round(client, storage, 2024, 25)

        client.get_race_results.return_value = _results(round_="25")
        client.get_qualifying.return_value = _empty_endpoint()
        client.get_pitstops.return_value = _empty_endpoint()
        client.get_sprint.return_value = _empty_endpoint()
        second_result = backfill.process_round(client, storage, 2024, 25)

        assert first_result == "skipped"
        assert second_result == "processed"
        assert storage.upsert_event.call_count == 1

    def test_pitstops_only_fetched_for_2011_and_later(self):
        client = MagicMock()
        client.get_race_results.return_value = _results()
        client.get_qualifying.return_value = _empty_endpoint()
        client.get_sprint.return_value = _empty_endpoint()
        storage = _storage()

        backfill.process_round(client, storage, 2010, 1)

        client.get_pitstops.assert_not_called()

    def test_pitstops_fetched_for_2011(self):
        client = MagicMock()
        client.get_race_results.return_value = _results()
        client.get_qualifying.return_value = _empty_endpoint()
        client.get_pitstops.return_value = _empty_endpoint()
        client.get_sprint.return_value = _empty_endpoint()
        storage = _storage()

        backfill.process_round(client, storage, 2011, 1)

        client.get_pitstops.assert_called_once_with(2011, 1)

    def test_a_real_sprint_result_is_cached_and_normalized_as_its_own_event(self):
        client = MagicMock()
        client.get_race_results.return_value = _results(round_="5")
        client.get_qualifying.return_value = _empty_endpoint()
        client.get_pitstops.return_value = _empty_endpoint()
        client.get_sprint.return_value = _sprint_results(round_="5")
        storage = _storage()

        backfill.process_round(client, storage, 2024, 5)

        written_keys = [c.args[0] for c in storage.put_raw_json.call_args_list]
        assert "f1/sprint/2024/5.json" in written_keys
        # 2 upsert_event calls -- the main race event AND the sprint event.
        assert storage.upsert_event.call_count == 2
        event_types = [c.args[0]["event_type"] for c in storage.upsert_event.call_args_list]
        assert "sprint" in event_types
        # 4 upsert_entity calls -- 1 driver + 1 constructor from the main
        # race, 1 driver + 1 constructor again from the sprint (same
        # driver/constructor, re-upserted -- harmless, idempotent).
        assert storage.upsert_entity.call_count == 4

    def test_a_cached_sprint_file_is_reused_and_still_normalized(self):
        client = MagicMock()
        client.get_race_results.return_value = _results(round_="5")
        client.get_qualifying.return_value = _empty_endpoint()
        client.get_pitstops.return_value = _empty_endpoint()
        storage = _storage(cached_keys=["f1/results/2024/5.json", "f1/sprint/2024/5.json"])
        storage.get_raw_json.side_effect = lambda key: (
            _results(round_="5") if key == "f1/results/2024/5.json" else _sprint_results(round_="5")
        )

        backfill.process_round(client, storage, 2024, 5)

        client.get_sprint.assert_not_called()
        assert storage.upsert_event.call_count == 2

    def test_a_non_sprint_weekend_does_not_write_a_sprint_file(self):
        client = MagicMock()
        client.get_race_results.return_value = _results()
        client.get_qualifying.return_value = _empty_endpoint()
        client.get_pitstops.return_value = _empty_endpoint()
        client.get_sprint.return_value = _empty_endpoint()
        storage = _storage()

        backfill.process_round(client, storage, 2024, 1)

        written_keys = [c.args[0] for c in storage.put_raw_json.call_args_list]
        assert not any("/sprint/" in k for k in written_keys)

    def test_a_failed_supplemental_fetch_does_not_raise(self):
        client = MagicMock()
        client.get_race_results.return_value = _results()
        client.get_qualifying.side_effect = Exception("network error")
        client.get_pitstops.return_value = _empty_endpoint()
        client.get_sprint.return_value = _empty_endpoint()
        storage = _storage()

        result = backfill.process_round(client, storage, 2024, 1)

        assert result == "processed"  # the real result was still written


class TestProcessRoundQualifyingMerge:
    def test_qualifying_is_merged_into_the_event_before_its_single_upsert(self):
        client = MagicMock()
        client.get_race_results.return_value = _results()
        client.get_qualifying.return_value = _qualifying_results()
        client.get_pitstops.return_value = _empty_endpoint()
        client.get_sprint.return_value = _empty_endpoint()
        storage = _storage()

        backfill.process_round(client, storage, 2024, 1)

        assert storage.upsert_event.call_count == 1
        event_item = storage.upsert_event.call_args.args[0]
        assert event_item["participants"][0]["result"]["qualifying"]["position"] == 1

    def test_qualifying_is_reused_from_s3_when_already_cached(self):
        client = MagicMock()
        client.get_race_results.return_value = _results()
        client.get_pitstops.return_value = _empty_endpoint()
        client.get_sprint.return_value = _empty_endpoint()
        storage = _storage(cached_keys=["f1/results/2024/1.json", "f1/qualifying/2024/1.json"])
        storage.get_raw_json.side_effect = lambda key: (
            _results() if key == "f1/results/2024/1.json" else _qualifying_results()
        )

        backfill.process_round(client, storage, 2024, 1)

        client.get_qualifying.assert_not_called()
        event_item = storage.upsert_event.call_args.args[0]
        assert event_item["participants"][0]["result"]["qualifying"]["position"] == 1

    def test_a_race_with_no_qualifying_data_yet_still_writes_the_event_with_none(self):
        client = MagicMock()
        client.get_race_results.return_value = _results()
        client.get_qualifying.return_value = _empty_endpoint()
        client.get_pitstops.return_value = _empty_endpoint()
        client.get_sprint.return_value = _empty_endpoint()
        storage = _storage()

        backfill.process_round(client, storage, 2024, 1)

        event_item = storage.upsert_event.call_args.args[0]
        assert event_item["participants"][0]["result"]["qualifying"] is None

    def test_an_empty_qualifying_response_is_never_cached_to_s3(self):
        # Regression, same bug class as the results-side test above --
        # caching an empty qualifying response would permanently freeze
        # this round's qualifying data as "unavailable" even after the
        # real session data exists.
        client = MagicMock()
        client.get_race_results.return_value = _results()
        client.get_qualifying.return_value = _empty_endpoint()
        client.get_pitstops.return_value = _empty_endpoint()
        client.get_sprint.return_value = _empty_endpoint()
        storage = _storage()

        backfill.process_round(client, storage, 2024, 1)

        written_keys = [c.args[0] for c in storage.put_raw_json.call_args_list]
        assert "f1/qualifying/2024/1.json" not in written_keys

    def test_qualifying_that_later_becomes_available_is_correctly_re_fetched(self):
        client = MagicMock()
        storage = _storage()  # nothing ever cached, matching a real not-yet-cached round

        client.get_race_results.return_value = _results()
        client.get_pitstops.return_value = _empty_endpoint()
        client.get_sprint.return_value = _empty_endpoint()
        client.get_qualifying.return_value = _empty_endpoint()
        backfill.process_round(client, storage, 2024, 1)
        first_event = storage.upsert_event.call_args.args[0]

        client.get_qualifying.return_value = _qualifying_results()
        backfill.process_round(client, storage, 2024, 1)
        second_event = storage.upsert_event.call_args.args[0]

        assert first_event["participants"][0]["result"]["qualifying"] is None
        assert second_event["participants"][0]["result"]["qualifying"]["position"] == 1


class TestProcessSeason:
    def test_writes_the_schedule_and_counts_processed_vs_skipped(self):
        client = MagicMock()
        client.get_races.return_value = _schedule([(1, "2024-03-02"), (2, "2024-03-09")])
        client.get_race_results.side_effect = [_results(round_="1"), _results(round_="2", has_results=False)]
        client.get_qualifying.return_value = _empty_endpoint()
        client.get_pitstops.return_value = _empty_endpoint()
        client.get_sprint.return_value = _empty_endpoint()
        storage = _storage()

        result = backfill.process_season(client, storage, 2024)

        assert result["rounds_processed"] == 1
        assert result["rounds_skipped"] == 1
        assert result["rounds_failed"] == 0
        storage.put_raw_json.assert_any_call("f1/schedule/2024.json", client.get_races.return_value)

    def test_one_rounds_failure_does_not_block_the_others(self):
        client = MagicMock()
        client.get_races.return_value = _schedule([(1, "2024-03-02"), (2, "2024-03-09")])
        client.get_race_results.side_effect = [Exception("boom"), _results(round_="2")]
        client.get_qualifying.return_value = _empty_endpoint()
        client.get_pitstops.return_value = _empty_endpoint()
        client.get_sprint.return_value = _empty_endpoint()
        storage = _storage()

        result = backfill.process_season(client, storage, 2024)

        assert result["rounds_processed"] == 1
        assert result["rounds_failed"] == 1
        assert len(result["failures"]) == 1
        assert result["failures"][0]["round"] == 1


class TestProcessBatch:
    def test_processes_every_season_in_the_batch(self):
        client = MagicMock()
        client.get_races.return_value = _schedule([])
        storage = _storage()

        results = backfill.process_batch(client, storage, [2010, 2011])

        assert [r["season"] for r in results] == [2010, 2011]
