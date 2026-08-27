"""
Unit tests for library.normalize.pga -- the field-event counterpart to
tests/library/normalize/test_espn_scoreboard_event.py etc. Every fixture
shape below mirrors a real ESPN golf leaderboard response field-for-field
(verified live 2026-08-24 against a finished event, a real missed-cut
event, and a real not-yet-started event -- see project-pga-onboarding
memory), not a guessed shape.
"""
import logging

import pytest

from library.normalize.pga import (
    is_flat_stroke_play,
    is_medal_scoring,
    is_team_stroke_play,
    leaderboard_event_to_event_item,
    leaderboard_event_to_player_entities,
)


def _competitor(
    athlete_id="10140", name="Xander Schauffele", status_name="STATUS_FINISH", completed=True,
    position_display="T26", is_tie=True, score_display="-4", score_value=276.0, earnings=177500.0,
    country="USA", amateur=False, linescores=None,
):
    return {
        "id": athlete_id,
        "earnings": earnings,
        "amateur": amateur,
        # amateur is redundantly nested inside athlete too on a real
        # response (confirmed live, 2026-08-26, on a real Masters
        # competitor -- 0 mismatches across 91 real competitors), which
        # is the copy leaderboard_event_to_player_entities actually reads
        # (it must, to also cover Zurich Classic's roster-nested athletes).
        "athlete": {"id": athlete_id, "displayName": name, "flag": {"alt": country}, "amateur": amateur},
        "status": {
            "type": {"id": "2", "name": status_name, "state": "post", "completed": completed},
            "position": {"id": "26", "displayName": position_display, "isTie": is_tie},
        },
        "score": {"value": score_value, "displayValue": score_display},
        "linescores": linescores if linescores is not None else [
            {"period": 1, "value": 68.0, "displayValue": "-2"},
            {"period": 2, "value": 70.0, "displayValue": "E"},
            {"period": 3, "value": 69.0, "displayValue": "-1"},
            {"period": 4, "value": 69.0, "displayValue": "-1"},
        ],
    }


def _team_stroke_competitor(
    golfer_ids=("125990", "125991"), names=("Chad Ramey", "Justin Suh"), status_name="STATUS_CUT",
    completed=False, score_display="-6", score_value=138.0, team_display="C. Ramey / J. Creel",
):
    """A Zurich Classic (Teamstroke) competitor -- a 2-golfer pairing
    keyed by `roster`, not `athlete`. Shape confirmed live, 2026-08-26,
    against a real Zurich Classic leaderboard response."""
    return {
        "id": golfer_ids[0],
        "earnings": 0.0,
        "status": {
            "type": {"id": "3", "name": status_name, "state": "post", "completed": completed},
            "position": {"id": "46", "displayName": "-", "isTie": False},
        },
        "score": {"value": score_value, "displayValue": score_display},
        "linescores": [
            {"period": 1, "value": 68.0, "displayValue": "-4"},
            {"period": 2, "value": 70.0, "displayValue": "-2"},
        ],
        "team": {"displayName": team_display},
        "roster": [
            {"playerId": gid, "athlete": {"id": gid, "displayName": name, "flag": {"alt": "USA"}, "amateur": False}}
            for gid, name in zip(golfer_ids, names)
        ],
    }


def _event(
    event_id="401811963", status_completed=True, status_name="STATUS_FINAL", competitors=None,
    purse=20000000, major=False, scoring_system="Medal", tournament_name="BMW Championship",
    cut_score=None, cut_round=None, cut_count=None,
):
    return {
        "id": event_id,
        "date": "2026-08-20T04:00Z",
        "endDate": "2026-08-23T04:00Z",
        "season": {"year": 2026},
        "seasonType": {"id": "2", "name": "Regular Season"},
        "week": {},
        "purse": purse,
        "tournament": {
            "displayName": tournament_name, "major": major, "scoringSystem": {"name": scoring_system},
            "cutScore": cut_score, "cutRound": cut_round, "cutCount": cut_count,
        },
        "status": {"type": {"name": status_name, "completed": status_completed}},
        "courses": [{
            "id": "65", "name": "Bellerive Country Club", "host": True,
            "address": {"city": "St. Louis", "state": "MO", "country": "USA"},
        }],
        "competitions": [{"competitors": competitors if competitors is not None else [_competitor()]}],
    }


class TestLeaderboardEventToEventItem:
    def test_has_required_schema_fields(self):
        item = leaderboard_event_to_event_item(_event(), "pga")
        for field in ("event_key", "event_id", "sport", "event_type", "event_date", "status", "participants", "season"):
            assert field in item, f"Missing field: {field}"

    def test_event_type_is_field(self):
        item = leaderboard_event_to_event_item(_event(), "pga")
        assert item["event_type"] == "field"

    def test_event_key_and_id(self):
        item = leaderboard_event_to_event_item(_event(event_id="401811963"), "pga")
        assert item["event_id"] == "401811963"
        assert item["event_key"] == "SPORT#PGA#EVENT#401811963"

    def test_event_date_is_truncated_to_the_date(self):
        item = leaderboard_event_to_event_item(_event(), "pga")
        assert item["event_date"] == "2026-08-20"

    def test_completed_status_maps_to_completed(self):
        item = leaderboard_event_to_event_item(_event(status_completed=True), "pga")
        assert item["status"] == "completed"

    def test_not_completed_status_maps_to_scheduled(self):
        item = leaderboard_event_to_event_item(_event(status_completed=False, status_name="STATUS_SCHEDULED"), "pga")
        assert item["status"] == "scheduled"

    def test_season_comes_from_season_year_not_season_type(self):
        # The leaderboard endpoint's own `season` is a bare {"year": ...}
        # with no "type" key, unlike the scoreboard endpoint's -- season_type
        # comes from the sibling top-level `seasonType` field instead.
        item = leaderboard_event_to_event_item(_event(), "pga")
        assert item["season"] == 2026
        assert item["season_type"] == "2"

    def test_week_is_none(self):
        item = leaderboard_event_to_event_item(_event(), "pga")
        assert item["week"] is None

    def test_venue_indoor_is_always_none(self):
        item = leaderboard_event_to_event_item(_event(), "pga")
        assert item["venue_indoor"] is None

    def test_venue_city_and_state_come_from_the_host_course_address(self):
        item = leaderboard_event_to_event_item(_event(), "pga")
        assert item["venue_city"] == "St. Louis"
        assert item["venue_state"] == "MO"
        assert item["venue_name"] == "Bellerive Country Club"

    def test_course_id_comes_from_the_host_course(self):
        item = leaderboard_event_to_event_item(_event(), "pga")
        assert item["course_id"] == "65"

    def test_international_course_has_no_state(self):
        event = _event()
        event["courses"][0]["address"] = {"city": "North Berwick", "country": "Scotland"}
        item = leaderboard_event_to_event_item(event, "pga")
        assert item["venue_city"] == "North Berwick"
        assert item["venue_state"] is None

    def test_participants_have_no_role_key(self):
        item = leaderboard_event_to_event_item(_event(), "pga")
        assert "role" not in item["participants"][0]

    def test_purse_passes_through(self):
        item = leaderboard_event_to_event_item(_event(purse=20000000), "pga")
        assert item["purse"] == 20000000

    def test_cut_fields_pass_through_for_a_real_cut_event(self):
        item = leaderboard_event_to_event_item(_event(cut_score=-2, cut_round=2, cut_count=71), "pga")
        assert item["cut_score"] == -2
        assert item["cut_round"] == 2
        assert item["cut_count"] == 71

    def test_cut_fields_are_zero_not_missing_for_a_no_cut_event(self):
        # A no-cut FedEx Cup playoff event reports all three as a real 0,
        # not absent -- cut-line training filters on cut_count > 0, not a
        # null check, specifically because of this.
        item = leaderboard_event_to_event_item(_event(cut_score=0, cut_round=0, cut_count=0), "pga")
        assert item["cut_score"] == 0
        assert item["cut_round"] == 0
        assert item["cut_count"] == 0

    def test_is_major_true_for_a_major_championship(self):
        item = leaderboard_event_to_event_item(_event(major=True), "pga")
        assert item["is_major"] is True

    def test_is_major_false_by_default(self):
        item = leaderboard_event_to_event_item(_event(major=False), "pga")
        assert item["is_major"] is False

    def test_raises_on_a_non_medal_scoring_event(self):
        # Ryder Cup/Presidents Cup/WGC Match Play/Zurich Classic -- see
        # is_medal_scoring's own docstring. Defense-in-depth: real callers
        # check is_medal_scoring(event) first and never reach this.
        event = _event(scoring_system="Match", tournament_name="Ryder Cup")
        with pytest.raises(ValueError, match="Ryder Cup"):
            leaderboard_event_to_event_item(event, "pga")

    def test_raises_when_tournament_object_is_absent_entirely(self):
        # Fail closed: no tournament/scoringSystem to confirm Medal from
        # at all (e.g. a just-added, not-yet-configured future calendar
        # entry -- confirmed live on a real Presidents Cup entry,
        # 2026-08-25) is treated the same as a confirmed non-Medal event.
        event = _event()
        del event["tournament"]
        with pytest.raises(ValueError):
            leaderboard_event_to_event_item(event, "pga")


class TestIsMedalScoring:
    def test_true_for_medal_scoring(self):
        assert is_medal_scoring(_event(scoring_system="Medal")) is True

    def test_false_for_match_play(self):
        assert is_medal_scoring(_event(scoring_system="Match")) is False

    def test_false_for_team_stroke_play(self):
        assert is_medal_scoring(_event(scoring_system="Teamstroke")) is False

    def test_false_when_tournament_is_absent(self):
        event = _event()
        del event["tournament"]
        assert is_medal_scoring(event) is False

    def test_false_when_scoring_system_is_absent(self):
        event = _event()
        del event["tournament"]["scoringSystem"]
        assert is_medal_scoring(event) is False


class TestIsTeamStrokePlay:
    def test_true_for_teamstroke(self):
        assert is_team_stroke_play(_event(scoring_system="Teamstroke")) is True

    def test_false_for_medal(self):
        assert is_team_stroke_play(_event(scoring_system="Medal")) is False

    def test_false_for_match(self):
        assert is_team_stroke_play(_event(scoring_system="Match")) is False


class TestIsFlatStrokePlay:
    def test_true_for_medal(self):
        assert is_flat_stroke_play(_event(scoring_system="Medal")) is True

    def test_true_for_teamstroke(self):
        assert is_flat_stroke_play(_event(scoring_system="Teamstroke")) is True

    def test_false_for_match(self):
        # Ryder Cup/Presidents Cup/WGC Match Play/The Match -- routed to
        # library/normalize/pga_matchplay.py instead.
        assert is_flat_stroke_play(_event(scoring_system="Match")) is False


class TestStubAthleteCompetitor:
    """A Medal-scoring competitor whose `athlete` dict has no "id" at all
    -- confirmed live, 2026-08-26/27, during the backfill re-run (a real
    KeyError crash reproduced this exact shape). Same "stub athlete" gap
    library/normalize/espn.py's box-score parsing already guards against
    for a DNP player -- this competitor contributes no participant row
    rather than crashing."""

    def _stub_competitor(self):
        competitor = _competitor()
        competitor["athlete"] = {"displayName": "TBD"}
        return competitor

    def test_stub_athlete_competitor_contributes_no_participant(self):
        event = _event(competitors=[self._stub_competitor(), _competitor(athlete_id="10141")])
        item = leaderboard_event_to_event_item(event, "pga")
        assert [p["entity_id"] for p in item["participants"]] == ["10141"]

    def test_stub_athlete_competitor_does_not_crash_on_its_own(self):
        event = _event(competitors=[self._stub_competitor()])
        item = leaderboard_event_to_event_item(event, "pga")
        assert item["participants"] == []


class TestTeamStrokePlayParticipants:
    """Zurich Classic (Teamstroke) -- each 2-golfer roster pairing
    expands into two participant rows sharing the pairing's own result."""

    def test_a_pairing_produces_two_participants(self):
        event = _event(scoring_system="Teamstroke", competitors=[_team_stroke_competitor()])
        item = leaderboard_event_to_event_item(event, "pga")
        assert [p["entity_id"] for p in item["participants"]] == ["125990", "125991"]

    def test_both_participants_share_the_pairings_result(self):
        event = _event(scoring_system="Teamstroke", competitors=[_team_stroke_competitor()])
        item = leaderboard_event_to_event_item(event, "pga")
        results = [p["result"] for p in item["participants"]]
        assert results[0] == results[1]
        assert results[0]["score_to_par"] == -6
        assert results[0]["status"] == "cut"

    def test_each_participant_lists_the_other_as_partner(self):
        event = _event(scoring_system="Teamstroke", competitors=[_team_stroke_competitor()])
        item = leaderboard_event_to_event_item(event, "pga")
        by_id = {p["entity_id"]: p for p in item["participants"]}
        assert by_id["125990"]["partner_entity_ids"] == ["125991"]
        assert by_id["125991"]["partner_entity_ids"] == ["125990"]

    def test_multiple_pairings_all_expand(self):
        event = _event(
            scoring_system="Teamstroke",
            competitors=[
                _team_stroke_competitor(golfer_ids=("1", "2")),
                _team_stroke_competitor(golfer_ids=("3", "4")),
            ],
        )
        item = leaderboard_event_to_event_item(event, "pga")
        assert [p["entity_id"] for p in item["participants"]] == ["1", "2", "3", "4"]

    def test_entities_are_built_from_both_roster_golfers(self):
        event = _event(scoring_system="Teamstroke", competitors=[_team_stroke_competitor()])
        entities = leaderboard_event_to_player_entities(event, "pga")
        assert [e["entity_id"] for e in entities] == ["125990", "125991"]
        assert entities[0]["entity_type"] == "player"
        assert entities[0]["name"] == "Chad Ramey"
        assert entities[0]["metadata"]["country"] == "USA"

    def test_event_item_stays_field_event_type(self):
        # Teamstroke plugs into the existing top-10/cutline/score/round
        # models unchanged -- it's still event_type "field", not a new type.
        event = _event(scoring_system="Teamstroke", competitors=[_team_stroke_competitor()])
        item = leaderboard_event_to_event_item(event, "pga")
        assert item["event_type"] == "field"


class TestParticipantResult:
    def test_finished_status_maps_to_finished(self):
        item = leaderboard_event_to_event_item(_event(competitors=[_competitor(status_name="STATUS_FINISH")]), "pga")
        assert item["participants"][0]["result"]["status"] == "finished"

    def test_cut_status_maps_to_cut(self):
        item = leaderboard_event_to_event_item(
            _event(competitors=[_competitor(status_name="STATUS_CUT", completed=False, position_display="-", is_tie=False)]), "pga",
        )
        result = item["participants"][0]["result"]
        assert result["status"] == "cut"

    def test_scheduled_status_maps_to_scheduled(self):
        item = leaderboard_event_to_event_item(
            _event(competitors=[_competitor(status_name="STATUS_SCHEDULED", completed=False)]), "pga",
        )
        assert item["participants"][0]["result"]["status"] == "scheduled"

    def test_mdf_status_maps_to_made_cut_did_not_finish(self):
        # "Made Cut Did Not Finish" -- a golfer who made the cut but
        # withdrew before finishing (e.g. injury mid-round-3). Confirmed
        # common (not rare) via a live sweep of 2017-2025 seasons,
        # 2026-08-26 -- explicitly mapped rather than left to the generic
        # fallback so it doesn't log a warning on every occurrence.
        item = leaderboard_event_to_event_item(
            _event(competitors=[_competitor(status_name="STATUS_MDF", completed=False, position_display="T80", is_tie=True)]), "pga",
        )
        assert item["participants"][0]["result"]["status"] == "made_cut_did_not_finish"

    def test_withdrawn_status_maps_to_withdrawn(self):
        # A golfer who withdrew before making the cut -- confirmed live
        # during the 2026-08-26/27 backfill re-run -- explicitly mapped
        # rather than left to the generic fallback so it doesn't log a
        # warning on every occurrence.
        item = leaderboard_event_to_event_item(
            _event(competitors=[_competitor(status_name="STATUS_WITHDRAWN")]), "pga",
        )
        assert item["participants"][0]["result"]["status"] == "withdrawn"

    def test_unmapped_status_falls_back_to_a_generic_transform_and_logs(self, caplog):
        with caplog.at_level(logging.WARNING):
            item = leaderboard_event_to_event_item(
                _event(competitors=[_competitor(status_name="STATUS_DISQUALIFIED")]), "pga",
            )
        assert item["participants"][0]["result"]["status"] == "disqualified"
        assert "Unmapped PGA status" in caplog.text

    def test_tied_finish_position_parses_number_and_is_tie(self):
        item = leaderboard_event_to_event_item(
            _event(competitors=[_competitor(position_display="T26", is_tie=True)]), "pga",
        )
        result = item["participants"][0]["result"]
        assert result["finish_position"] == 26
        assert result["is_tie"] is True

    def test_solo_finish_position_parses_number_without_tie(self):
        item = leaderboard_event_to_event_item(
            _event(competitors=[_competitor(position_display="1", is_tie=False)]), "pga",
        )
        result = item["participants"][0]["result"]
        assert result["finish_position"] == 1
        assert result["is_tie"] is False

    def test_no_finish_position_is_none(self):
        item = leaderboard_event_to_event_item(
            _event(competitors=[_competitor(position_display="-", is_tie=False)]), "pga",
        )
        assert item["participants"][0]["result"]["finish_position"] is None

    def test_negative_score_to_par_parses_as_a_negative_int(self):
        item = leaderboard_event_to_event_item(
            _event(competitors=[_competitor(score_display="-17", score_value=267.0)]), "pga",
        )
        result = item["participants"][0]["result"]
        assert result["score_to_par"] == -17
        assert result["total_strokes"] == 267.0

    def test_positive_score_to_par_parses_as_a_positive_int(self):
        item = leaderboard_event_to_event_item(
            _event(competitors=[_competitor(score_display="+2", score_value=282.0)]), "pga",
        )
        assert item["participants"][0]["result"]["score_to_par"] == 2

    def test_even_par_display_value_e_parses_as_zero(self):
        item = leaderboard_event_to_event_item(
            _event(competitors=[_competitor(score_display="E", score_value=280.0)]), "pga",
        )
        result = item["participants"][0]["result"]
        assert result["score_to_par"] == 0
        assert result["total_strokes"] == 280.0

    def test_not_yet_played_score_of_dash_and_sentinel_zero_parses_as_none_none(self):
        # A not-yet-started tournament pre-lists every competitor with
        # score {"value": 0.0, "displayValue": "-"} -- confirmed live,
        # 0.0 there is a sentinel, not a real 0-stroke round.
        item = leaderboard_event_to_event_item(
            _event(competitors=[_competitor(score_display="-", score_value=0.0)]), "pga",
        )
        result = item["participants"][0]["result"]
        assert result["score_to_par"] is None
        assert result["total_strokes"] is None

    def test_earnings_passes_through(self):
        item = leaderboard_event_to_event_item(
            _event(competitors=[_competitor(earnings=177500.0)]), "pga",
        )
        assert item["participants"][0]["result"]["earnings"] == 177500.0

    def test_missed_cut_earnings_is_zero(self):
        item = leaderboard_event_to_event_item(
            _event(competitors=[_competitor(status_name="STATUS_CUT", earnings=0.0)]), "pga",
        )
        assert item["participants"][0]["result"]["earnings"] == 0.0


class TestParticipantRounds:
    def test_one_entry_per_round_played(self):
        item = leaderboard_event_to_event_item(_event(), "pga")
        rounds = item["participants"][0]["result"]["rounds"]
        assert [r["round"] for r in rounds] == [1, 2, 3, 4]

    def test_each_rounds_score_to_par_and_total_strokes(self):
        item = leaderboard_event_to_event_item(_event(), "pga")
        rounds = item["participants"][0]["result"]["rounds"]
        assert rounds[0] == {"round": 1, "score_to_par": -2, "total_strokes": 68.0}

    def test_even_par_round_parses_e_as_zero(self):
        item = leaderboard_event_to_event_item(_event(), "pga")
        rounds = item["participants"][0]["result"]["rounds"]
        assert rounds[1] == {"round": 2, "score_to_par": 0, "total_strokes": 70.0}

    def test_a_cut_golfer_only_has_rounds_1_and_2(self):
        cut_competitor = _competitor(
            status_name="STATUS_CUT", position_display="-", is_tie=False,
            linescores=[
                {"period": 1, "value": 74.0, "displayValue": "+4"},
                {"period": 2, "value": 72.0, "displayValue": "+2"},
            ],
        )
        item = leaderboard_event_to_event_item(_event(competitors=[cut_competitor]), "pga")
        rounds = item["participants"][0]["result"]["rounds"]
        assert [r["round"] for r in rounds] == [1, 2]

    def test_no_linescores_at_all_yields_an_empty_rounds_list(self):
        no_rounds_competitor = _competitor(linescores=[])
        item = leaderboard_event_to_event_item(_event(competitors=[no_rounds_competitor]), "pga")
        assert item["participants"][0]["result"]["rounds"] == []


class TestLeaderboardEventToPlayerEntities:
    def test_raises_on_a_non_medal_scoring_event(self):
        # Teamstroke (Zurich Classic) is a SUPPORTED flat-stroke-play
        # format (is_flat_stroke_play), unlike Match-scored team/
        # individual match play -- see is_flat_stroke_play's own
        # docstring. Match/Ryder Cup is the real "unsupported" example.
        event = _event(scoring_system="Match", tournament_name="Ryder Cup")
        with pytest.raises(ValueError, match="Ryder Cup"):
            leaderboard_event_to_player_entities(event, "pga")

    def test_entity_has_required_schema_fields(self):
        entities = leaderboard_event_to_player_entities(_event(), "pga")
        for field in ("entity_key", "entity_id", "sport", "entity_type", "name", "metadata"):
            assert field in entities[0], f"Missing field: {field}"

    def test_entity_type_is_player_not_team(self):
        entities = leaderboard_event_to_player_entities(_event(), "pga")
        assert entities[0]["entity_type"] == "player"

    def test_entity_key_uses_player_type(self):
        entities = leaderboard_event_to_player_entities(_event(competitors=[_competitor(athlete_id="10140")]), "pga")
        assert entities[0]["entity_key"] == "SPORT#PGA#ENTITY#PLAYER#10140"

    def test_name_and_country_and_amateur(self):
        entities = leaderboard_event_to_player_entities(
            _event(competitors=[_competitor(name="Xander Schauffele", country="USA", amateur=False)]), "pga",
        )
        assert entities[0]["name"] == "Xander Schauffele"
        assert entities[0]["metadata"]["country"] == "USA"
        assert entities[0]["metadata"]["amateur"] is False

    def test_amateur_competitor_is_flagged(self):
        entities = leaderboard_event_to_player_entities(_event(competitors=[_competitor(amateur=True)]), "pga")
        assert entities[0]["metadata"]["amateur"] is True

    def test_each_competitor_produces_its_own_entity(self):
        competitors = [_competitor(athlete_id="1"), _competitor(athlete_id="2")]
        entities = leaderboard_event_to_player_entities(_event(competitors=competitors), "pga")
        assert [e["entity_id"] for e in entities] == ["1", "2"]

    def test_a_competitor_with_no_athlete_id_is_skipped(self):
        broken = _competitor(athlete_id="1")
        broken["athlete"] = {"displayName": "No Id"}
        entities = leaderboard_event_to_player_entities(
            _event(competitors=[broken, _competitor(athlete_id="2")]), "pga",
        )
        assert [e["entity_id"] for e in entities] == ["2"]
