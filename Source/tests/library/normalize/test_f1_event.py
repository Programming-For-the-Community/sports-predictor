"""
Unit tests for library.normalize.f1. Fixture payload shapes below are
trimmed from real Jolpica-F1 responses fetched live 2026-08-30
(http://api.jolpi.ca/ergast/f1/2024/1/results.json and .../2024/3/
results.json) -- a normal finisher, a classified-but-retired finisher
(covered enough distance to be classified despite status "Retired"), and
two genuinely unclassified retirements at different lap counts, all real
rows from those two races, per this project's own "verify raw fields
before feature code" rule.
"""
import pytest

from library.normalize.f1 import (
    map_status,
    merge_qualifying_into_event,
    qualifying_payload_to_results,
    race_result_to_constructor_entities,
    race_result_to_driver_entities,
    race_result_to_event_item,
    sprint_result_to_constructor_entities,
    sprint_result_to_driver_entities,
    sprint_result_to_event_item,
)

_WINNER = {
    "number": "1", "position": "1", "positionText": "1", "points": "26",
    "Driver": {
        "driverId": "max_verstappen", "permanentNumber": "3", "code": "VER",
        "givenName": "Max", "familyName": "Verstappen", "dateOfBirth": "1997-09-30", "nationality": "Dutch",
    },
    "Constructor": {"constructorId": "red_bull", "name": "Red Bull", "nationality": "Austrian"},
    "grid": "1", "laps": "57", "status": "Finished",
    "FastestLap": {"rank": "1", "lap": "39", "Time": {"time": "1:32.608"}},
}
_CLASSIFIED_RETIREMENT = {
    "number": "63", "position": "17", "positionText": "17", "points": "0",
    "Driver": {
        "driverId": "russell", "permanentNumber": "63", "code": "RUS",
        "givenName": "George", "familyName": "Russell", "dateOfBirth": "1998-02-15", "nationality": "British",
    },
    "Constructor": {"constructorId": "mercedes", "name": "Mercedes", "nationality": "German"},
    "grid": "7", "laps": "56", "status": "Retired",
    "FastestLap": {"rank": "5", "lap": "53", "Time": {"time": "1:20.284"}},
}
_UNCLASSIFIED_RETIREMENT = {
    "number": "1", "position": "19", "positionText": "R", "points": "0",
    "Driver": {
        "driverId": "max_verstappen", "permanentNumber": "3", "code": "VER",
        "givenName": "Max", "familyName": "Verstappen", "dateOfBirth": "1997-09-30", "nationality": "Dutch",
    },
    "Constructor": {"constructorId": "red_bull", "name": "Red Bull", "nationality": "Austrian"},
    "grid": "1", "laps": "3", "status": "Retired",
    "FastestLap": {"rank": "19", "lap": "3", "Time": {"time": "1:23.115"}},
}


def _payload(*results, season="2024", round_="1", event_date="2024-03-02"):
    return {
        "MRData": {
            "RaceTable": {
                "season": season, "round": round_,
                "Races": [{
                    "season": season, "round": round_, "raceName": "Bahrain Grand Prix",
                    "Circuit": {
                        "circuitId": "bahrain", "circuitName": "Bahrain International Circuit",
                        "Location": {"locality": "Sakhir", "country": "Bahrain"},
                    },
                    "date": event_date,
                    "Results": list(results),
                }],
            },
        },
    }


class TestMapStatus:
    def test_finished_maps_directly_by_name(self):
        assert map_status("Finished", "1") == "finished"

    def test_a_digit_position_text_with_a_non_finished_status_is_classified(self):
        # Real case: George Russell, 2024 Australian GP -- 56/57 laps,
        # status "Retired", but still classified (positionText "17").
        assert map_status("Retired", "17") == "classified"
        assert map_status("Lapped", "12") == "classified"

    def test_letter_position_text_r_is_an_unclassified_dnf(self):
        # Real case: Max Verstappen, 2024 Australian GP -- 3 laps,
        # status "Retired", positionText "R".
        assert map_status("Retired", "R") == "dnf"

    def test_disqualified_maps_directly_regardless_of_position_text(self):
        assert map_status("Disqualified", "D") == "dsq"

    def test_did_not_start_maps_to_dns(self):
        assert map_status("Did not start", "W") == "dns"

    def test_an_unrecognized_letter_code_fails_open_to_dnf(self):
        assert map_status("Some Future Reason", "Z") == "dnf"


class TestRaceResultToEventItem:
    def test_builds_the_event_item_with_real_field_names(self):
        payload = _payload(_WINNER, _CLASSIFIED_RETIREMENT)

        item = race_result_to_event_item(payload, "f1")

        assert item["event_key"] == "SPORT#F1#EVENT#2024-1"
        assert item["event_id"] == "2024-1"
        assert item["sport"] == "f1"
        assert item["event_type"] == "field"
        assert item["event_date"] == "2024-03-02"
        assert item["status"] == "completed"
        assert item["season"] == 2024
        assert item["week"] == 1
        assert item["circuit_id"] == "bahrain"
        assert item["venue_name"] == "Bahrain International Circuit"
        assert item["venue_city"] == "Sakhir"
        assert item["venue_state"] == "Bahrain"
        assert item["race_name"] == "Bahrain Grand Prix"
        assert len(item["participants"]) == 2

    def test_the_winner_participant_carries_a_full_classified_result(self):
        payload = _payload(_WINNER)

        item = race_result_to_event_item(payload, "f1")
        result = item["participants"][0]

        assert result["entity_id"] == "max_verstappen"
        assert result["constructor_entity_id"] == "red_bull"
        assert result["result"]["finish_position"] == 1
        assert result["result"]["grid_position"] == 1
        assert result["result"]["status"] == "finished"
        assert result["result"]["points"] == 26.0
        assert result["result"]["fastest_lap"] is True
        assert result["result"]["laps_completed"] == 57

    def test_a_classified_but_retired_result_gets_a_real_finish_position_and_no_points(self):
        payload = _payload(_CLASSIFIED_RETIREMENT)

        result = race_result_to_event_item(payload, "f1")["participants"][0]

        assert result["result"]["finish_position"] == 17
        assert result["result"]["status"] == "classified"
        assert result["result"]["points"] == 0.0
        assert result["result"]["fastest_lap"] is False

    def test_an_unclassified_dnf_has_no_finish_position_at_all(self):
        payload = _payload(_UNCLASSIFIED_RETIREMENT)

        result = race_result_to_event_item(payload, "f1")["participants"][0]

        assert result["result"]["finish_position"] is None
        assert result["result"]["status"] == "dnf"
        assert result["result"]["laps_completed"] == 3

    def test_no_races_in_payload_raises(self):
        with pytest.raises(ValueError):
            race_result_to_event_item({"MRData": {"RaceTable": {"Races": []}}}, "f1")


class TestRaceResultToDriverEntities:
    def test_builds_one_player_entity_per_driver_deduplicated(self):
        payload = _payload(_WINNER, _UNCLASSIFIED_RETIREMENT)  # same driver twice

        entities = race_result_to_driver_entities(payload, "f1")

        assert len(entities) == 1
        entity = entities[0]
        assert entity["entity_key"] == "SPORT#F1#ENTITY#PLAYER#max_verstappen"
        assert entity["entity_type"] == "player"
        assert entity["name"] == "Max Verstappen"
        assert entity["metadata"]["code"] == "VER"
        assert entity["metadata"]["nationality"] == "Dutch"

    def test_empty_payload_returns_no_entities(self):
        assert race_result_to_driver_entities({"MRData": {"RaceTable": {"Races": []}}}, "f1") == []


class TestRaceResultToConstructorEntities:
    def test_builds_one_team_entity_per_constructor_deduplicated(self):
        payload = _payload(_WINNER, _UNCLASSIFIED_RETIREMENT)  # same constructor twice

        entities = race_result_to_constructor_entities(payload, "f1")

        assert len(entities) == 1
        entity = entities[0]
        assert entity["entity_key"] == "SPORT#F1#ENTITY#TEAM#red_bull"
        assert entity["entity_type"] == "team"
        assert entity["name"] == "Red Bull"
        assert entity["metadata"]["nationality"] == "Austrian"

    def test_a_type_aware_key_avoids_colliding_with_a_driver_of_the_same_raw_id(self):
        entities = race_result_to_constructor_entities(_payload(_WINNER), "f1")
        assert entities[0]["entity_key"] != "SPORT#F1#ENTITY#PLAYER#red_bull"


def _sprint_payload(*results, season="2024", round_="5", event_date="2024-04-21"):
    return {
        "MRData": {
            "RaceTable": {
                "season": season, "round": round_,
                "Races": [{
                    "season": season, "round": round_, "raceName": "Chinese Grand Prix",
                    "Circuit": {
                        "circuitId": "shanghai", "circuitName": "Shanghai International Circuit",
                        "Location": {"locality": "Shanghai", "country": "China"},
                    },
                    "date": event_date,
                    "SprintResults": list(results),
                }],
            },
        },
    }


class TestSprintResultToEventItem:
    def test_builds_a_sprint_event_with_a_distinct_id_and_type(self):
        payload = _sprint_payload(_WINNER)

        item = sprint_result_to_event_item(payload, "f1")

        assert item["event_id"] == "2024-5-sprint"
        assert item["event_key"] == "SPORT#F1#EVENT#2024-5-sprint"
        assert item["event_type"] == "sprint"
        assert item["circuit_id"] == "shanghai"
        assert item["participants"][0]["entity_id"] == "max_verstappen"

    def test_a_sprint_event_id_never_collides_with_the_same_weekends_main_race(self):
        main_item = race_result_to_event_item(_payload(_WINNER, round_="5"), "f1")
        sprint_item = sprint_result_to_event_item(_sprint_payload(_WINNER), "f1")

        assert main_item["event_key"] != sprint_item["event_key"]

    def test_no_races_in_payload_raises(self):
        with pytest.raises(ValueError):
            sprint_result_to_event_item({"MRData": {"RaceTable": {"Races": []}}}, "f1")


class TestSprintEntities:
    def test_driver_and_constructor_entities_come_from_sprintresults(self):
        payload = _sprint_payload(_WINNER)

        drivers = sprint_result_to_driver_entities(payload, "f1")
        constructors = sprint_result_to_constructor_entities(payload, "f1")

        assert drivers[0]["entity_id"] == "max_verstappen"
        assert constructors[0]["entity_id"] == "red_bull"

    def test_empty_payload_returns_no_entities(self):
        assert sprint_result_to_driver_entities({"MRData": {"RaceTable": {"Races": []}}}, "f1") == []
        assert sprint_result_to_constructor_entities({"MRData": {"RaceTable": {"Races": []}}}, "f1") == []


# Real trimmed rows from a real 2024 qualifying response (2026-08-31):
# the pole-sitter (all 3 segments), a driver eliminated in Q2 (Q1+Q2
# only, no Q3 key at all), and a driver eliminated in Q1 (Q1 only, no
# Q2/Q3 keys at all -- confirmed live, not merely null).
_POLE = {
    "position": "1",
    "Driver": {"driverId": "max_verstappen"}, "Constructor": {"constructorId": "red_bull"},
    "Q1": "1:30.031", "Q2": "1:29.374", "Q3": "1:29.179",
}
_Q2_ELIMINATED = {
    "position": "12",
    "Driver": {"driverId": "hulkenberg"}, "Constructor": {"constructorId": "haas"},
    "Q1": "1:30.500", "Q2": "1:30.900",
}
_Q1_ELIMINATED = {
    "position": "18",
    "Driver": {"driverId": "sargeant"}, "Constructor": {"constructorId": "williams"},
    "Q1": "1:30.770",
}


def _qualifying_payload(*results, season="2024", round_="1"):
    return {
        "MRData": {
            "RaceTable": {
                "season": season, "round": round_,
                "Races": [{"season": season, "round": round_, "QualifyingResults": list(results)}],
            },
        },
    }


class TestQualifyingPayloadToResults:
    def test_parses_mm_ss_lap_times_to_seconds(self):
        results = qualifying_payload_to_results(_qualifying_payload(_POLE))
        # 1:29.179 -> 89.179 seconds.
        assert results["max_verstappen"]["q3_seconds"] == pytest.approx(89.179)

    def test_parses_bare_sub_minute_lap_times_with_no_colon(self):
        # Regression: a real backfill run (2026-08-31) logged dozens of
        # genuinely valid sub-minute qualifying times ("54.963" etc, no
        # "M:" prefix at all) as "unparseable" and silently discarded
        # them -- the original parser assumed every value had a colon.
        short_lap = {
            "position": "1",
            "Driver": {"driverId": "d"}, "Constructor": {"constructorId": "c"},
            "Q1": "54.963",
        }
        results = qualifying_payload_to_results(_qualifying_payload(short_lap))
        assert results["d"]["q1_seconds"] == pytest.approx(54.963)
        assert results["d"]["best_seconds"] == pytest.approx(54.963)

    def test_best_seconds_is_the_deepest_segment_actually_reached(self):
        results = qualifying_payload_to_results(_qualifying_payload(_POLE, _Q2_ELIMINATED, _Q1_ELIMINATED))

        assert results["max_verstappen"]["best_seconds"] == pytest.approx(89.179)  # Q3
        assert results["hulkenberg"]["best_seconds"] == pytest.approx(90.9)  # Q2, no Q3 at all
        assert results["sargeant"]["best_seconds"] == pytest.approx(90.77)  # Q1 only

    def test_a_q1_eliminated_driver_has_no_q2_or_q3_value(self):
        results = qualifying_payload_to_results(_qualifying_payload(_Q1_ELIMINATED))
        assert results["sargeant"]["q2_seconds"] is None
        assert results["sargeant"]["q3_seconds"] is None

    def test_gap_to_pole_is_zero_for_the_pole_sitter_and_positive_for_everyone_else(self):
        results = qualifying_payload_to_results(_qualifying_payload(_POLE, _Q2_ELIMINATED))

        assert results["max_verstappen"]["gap_to_pole_seconds"] == 0.0
        assert results["hulkenberg"]["gap_to_pole_seconds"] > 0

    def test_no_races_returns_empty_dict(self):
        assert qualifying_payload_to_results({"MRData": {"RaceTable": {"Races": []}}}) == {}

    def test_an_unparseable_time_is_treated_as_missing_not_a_crash(self):
        bad = {"position": "1", "Driver": {"driverId": "d"}, "Constructor": {"constructorId": "c"}, "Q1": "garbage"}
        results = qualifying_payload_to_results(_qualifying_payload(bad))
        assert results["d"]["q1_seconds"] is None
        assert results["d"]["best_seconds"] is None


class TestMergeQualifyingIntoEvent:
    def test_merges_qualifying_onto_matching_participants_by_entity_id(self):
        event_item = race_result_to_event_item(_payload(_WINNER), "f1")
        qualifying_payload = _qualifying_payload(_POLE)

        merged = merge_qualifying_into_event(event_item, qualifying_payload)

        assert merged["participants"][0]["result"]["qualifying"]["position"] == 1
        assert merged is event_item  # mutates and returns the same dict

    def test_a_participant_with_no_matching_qualifying_row_gets_none_not_a_missing_key(self):
        event_item = race_result_to_event_item(_payload(_WINNER), "f1")  # driver: max_verstappen
        qualifying_payload = _qualifying_payload(_Q1_ELIMINATED)  # driver: sargeant, no overlap

        merged = merge_qualifying_into_event(event_item, qualifying_payload)

        assert "qualifying" in merged["participants"][0]["result"]
        assert merged["participants"][0]["result"]["qualifying"] is None

    def test_a_none_qualifying_payload_sets_every_participant_to_none(self):
        event_item = race_result_to_event_item(_payload(_WINNER), "f1")

        merged = merge_qualifying_into_event(event_item, None)

        assert merged["participants"][0]["result"]["qualifying"] is None
