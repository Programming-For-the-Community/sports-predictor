"""
Unit tests for library.features.pga -- rolling_golfer_averages,
build_golfer_event_features, and the round-level/cut-line counterparts
added 2026-08-25, the field-event counterpart to library.features.common's
head-to-head rolling helpers.
"""
from library.features.pga import (
    SEASON_STAT_CATEGORIES,
    build_cup_event_features,
    build_cutline_event_features,
    build_golfer_event_features,
    build_match_event_features,
    build_round_event_features,
    rolling_golfer_averages,
    rolling_round_averages,
)


def _result(finish_position=None, score_to_par=None, earnings=None):
    return {"finish_position": finish_position, "score_to_par": score_to_par, "earnings": earnings}


def _round(round_number, score_to_par=None, total_strokes=None):
    return {"round": round_number, "score_to_par": score_to_par, "total_strokes": total_strokes}


class TestRollingGolferAverages:
    def test_no_history_returns_all_none_and_zero_events_played(self):
        averages = rolling_golfer_averages([])
        assert averages == {
            "avg_score_to_par": None,
            "avg_finish_position": None,
            "best_finish_position": None,
            "top_10_rate": None,
            "top_20_rate": None,
            "finish_rate": None,
            "avg_earnings": None,
            "events_played": 0,
        }

    def test_averages_score_to_par_and_finish_position_over_real_finishes(self):
        history = [
            _result(finish_position=5, score_to_par=-10, earnings=100000),
            _result(finish_position=15, score_to_par=-4, earnings=20000),
        ]
        averages = rolling_golfer_averages(history)
        assert averages["avg_score_to_par"] == -7
        assert averages["avg_finish_position"] == 10
        assert averages["avg_earnings"] == 60000
        assert averages["best_finish_position"] == 5

    def test_top_10_and_top_20_rate_are_divided_by_starts_not_just_finishes(self):
        # 3 starts: one top-10, one top-20-but-not-top-10, one missed cut
        # (no finish_position at all) -- the missed cut still counts
        # against the rate's denominator.
        history = [
            _result(finish_position=3, score_to_par=-8, earnings=50000),
            _result(finish_position=18, score_to_par=-1, earnings=10000),
            _result(finish_position=None, score_to_par=2, earnings=0),
        ]
        averages = rolling_golfer_averages(history)
        assert averages["top_10_rate"] == 1 / 3
        assert averages["top_20_rate"] == 2 / 3
        assert averages["finish_rate"] == 2 / 3
        assert averages["events_played"] == 3

    def test_missed_cut_still_contributes_its_score_to_par_average(self):
        # A cut golfer has a real score_to_par for the rounds they did
        # play, even with no finish_position.
        history = [_result(finish_position=None, score_to_par=4, earnings=0)]
        averages = rolling_golfer_averages(history)
        assert averages["avg_score_to_par"] == 4
        assert averages["avg_finish_position"] is None
        assert averages["best_finish_position"] is None

    def test_history_beyond_window_is_ignored(self):
        history = [_result(finish_position=1, score_to_par=-20, earnings=1000000)] + [
            _result(finish_position=50, score_to_par=5, earnings=0) for _ in range(5)
        ]
        averages = rolling_golfer_averages(history, window=1)
        assert averages["events_played"] == 1
        assert averages["best_finish_position"] == 1

    def test_a_row_with_no_score_to_par_and_no_earnings_is_excluded_from_those_averages_not_treated_as_zero(self):
        history = [_result(finish_position=None, score_to_par=None, earnings=None)]
        averages = rolling_golfer_averages(history)
        assert averages["avg_score_to_par"] is None
        assert averages["avg_earnings"] is None
        assert averages["events_played"] == 1  # still a real start


class TestBuildGolferEventFeatures:
    def _event(self, participants=None):
        return {
            "event_key": "SPORT#PGA#EVENT#1", "event_date": "2026-08-20",
            "purse": 20000000, "is_major": False,
            "participants": participants or [{"entity_id": "1"}, {"entity_id": "2"}, {"entity_id": "3"}],
        }

    def test_identifier_and_context_fields(self):
        event = self._event()
        participant = {"entity_id": "1", "result": {"finish_position": 5}}

        row = build_golfer_event_features(event, participant, [])

        assert row["event_key"] == "SPORT#PGA#EVENT#1"
        assert row["entity_id"] == "1"
        assert row["event_date"] == "2026-08-20"
        assert row["purse"] == 20000000
        assert row["is_major"] is False
        assert row["field_size"] == 3

    def test_label_top_10_is_1_for_a_top_10_finish(self):
        event = self._event()
        participant = {"entity_id": "1", "result": {"finish_position": 10}}

        row = build_golfer_event_features(event, participant, [])

        assert row["label_top_10"] == 1

    def test_label_top_10_is_0_for_an_11th_place_finish(self):
        event = self._event()
        participant = {"entity_id": "1", "result": {"finish_position": 11}}

        row = build_golfer_event_features(event, participant, [])

        assert row["label_top_10"] == 0

    def test_label_top_5_is_1_for_a_top_5_finish(self):
        event = self._event()
        participant = {"entity_id": "1", "result": {"finish_position": 5}}

        row = build_golfer_event_features(event, participant, [])

        assert row["label_top_5"] == 1

    def test_label_top_5_is_0_for_a_6th_place_finish(self):
        event = self._event()
        participant = {"entity_id": "1", "result": {"finish_position": 6}}

        row = build_golfer_event_features(event, participant, [])

        assert row["label_top_5"] == 0
        assert row["label_top_10"] == 1  # still a real top-10, just not top-5

    def test_label_top_10_is_0_for_a_missed_cut(self):
        event = self._event()
        participant = {"entity_id": "1", "result": {"finish_position": None}}

        row = build_golfer_event_features(event, participant, [])

        assert row["label_top_10"] == 0

    def test_missing_result_key_is_treated_as_no_finish_not_an_error(self):
        event = self._event()
        participant = {"entity_id": "1"}  # no "result" key at all

        row = build_golfer_event_features(event, participant, [])

        assert row["label_top_10"] == 0

    def test_rolling_averages_are_folded_in_from_prior_results(self):
        event = self._event()
        participant = {"entity_id": "1", "result": {"finish_position": 5}}
        prior_results = [_result(finish_position=1, score_to_par=-15, earnings=200000)]

        row = build_golfer_event_features(event, participant, prior_results)

        assert row["avg_finish_position"] == 1
        assert row["events_played"] == 1

    def test_is_major_true_passes_through(self):
        event = self._event()
        event["is_major"] = True
        participant = {"entity_id": "1", "result": {"finish_position": 1}}

        row = build_golfer_event_features(event, participant, [])

        assert row["is_major"] is True

    def test_course_fit_columns_are_prefixed_and_folded_in_from_course_results(self):
        event = self._event()
        participant = {"entity_id": "1", "result": {"finish_position": 5}}
        course_results = [_result(finish_position=2, score_to_par=-12, earnings=300000)]

        row = build_golfer_event_features(event, participant, [], course_results=course_results)

        assert row["course_avg_finish_position"] == 2
        assert row["course_best_finish_position"] == 2
        assert row["course_events_played"] == 1

    def test_label_score_to_par_comes_from_this_participants_own_result(self):
        event = self._event()
        participant = {"entity_id": "1", "result": {"finish_position": 5, "score_to_par": -8}}

        row = build_golfer_event_features(event, participant, [])

        assert row["label_score_to_par"] == -8

    def test_label_score_to_par_is_none_for_a_golfer_with_no_recorded_score(self):
        event = self._event()
        participant = {"entity_id": "1", "result": {"finish_position": None, "score_to_par": None}}

        row = build_golfer_event_features(event, participant, [])

        assert row["label_score_to_par"] is None

    def test_season_stat_columns_are_present_but_none_when_no_snapshot_given(self):
        event = self._event()
        participant = {"entity_id": "1", "result": {"finish_position": 5}}

        row = build_golfer_event_features(event, participant, [])

        for column_name in SEASON_STAT_CATEGORIES.values():
            assert row[column_name] is None

    def test_season_stat_columns_are_folded_in_from_season_stats(self):
        event = self._event()
        participant = {"entity_id": "1", "result": {"finish_position": 5}}
        season_stats = {"yardsPerDrive": 305.4, "greensInRegPct": 68.2}

        row = build_golfer_event_features(event, participant, [], season_stats=season_stats)

        assert row["season_driving_distance"] == 305.4
        assert row["season_gir_pct"] == 68.2
        assert row["season_scoring_average"] is None  # not in this snapshot's categories

    def test_no_course_results_still_produces_course_columns_as_missing_not_omitted(self):
        # course_results=None (the default) -- a caller with no course_id
        # to key by, or a golfer with zero prior appearances at this
        # course -- must still get every course_* column, just as missing
        # values, not a differently-shaped row Parquet's later
        # union-of-columns write would otherwise mask.
        event = self._event()
        participant = {"entity_id": "1", "result": {"finish_position": 5}}

        row = build_golfer_event_features(event, participant, [])

        assert row["course_avg_finish_position"] is None
        assert row["course_events_played"] == 0

    def test_overall_and_course_fit_history_are_independent(self):
        event = self._event()
        participant = {"entity_id": "1", "result": {"finish_position": 5}}
        prior_results = [_result(finish_position=1, score_to_par=-15, earnings=200000)]
        course_results = [_result(finish_position=40, score_to_par=8, earnings=0)]

        row = build_golfer_event_features(event, participant, prior_results, course_results=course_results)

        assert row["avg_finish_position"] == 1  # overall form
        assert row["course_avg_finish_position"] == 40  # a much worse history at this specific course

    def test_dirty_non_numeric_stored_values_are_coerced_to_none_not_left_as_strings(self):
        # Confirmed live, 2026-08-27: a row already sitting in DynamoDB
        # from before library/normalize/pga.py's _parse_score fix shipped
        # (a real empty-string score_to_par) crashed the ENTIRE dataset's
        # Parquet write with pyarrow.lib.ArrowInvalid -- one row's bad
        # value poisons the whole column, not just that row. A normalizer
        # fix alone can't protect against data already written before it
        # existed, so every raw passthrough field here must coerce
        # defensively too, not just trust the normalizer stayed clean.
        event = self._event()
        event["purse"] = ""
        participant = {"entity_id": "1", "result": {"finish_position": "", "score_to_par": ""}}
        season_stats = {"yardsPerDrive": ""}

        row = build_golfer_event_features(event, participant, [], season_stats=season_stats)

        assert row["purse"] is None
        assert row["label_score_to_par"] is None
        assert row["label_top_10"] == 0  # a non-numeric finish_position is treated as no finish, not a crash
        assert row["season_driving_distance"] is None


class TestRollingRoundAverages:
    def test_no_history_returns_none_and_zero_rounds_played(self):
        averages = rolling_round_averages([])
        assert averages == {"avg_score_to_par": None, "rounds_played": 0}

    def test_averages_score_to_par_over_past_rounds(self):
        history = [_round(1, score_to_par=-2), _round(1, score_to_par=-4)]
        averages = rolling_round_averages(history)
        assert averages["avg_score_to_par"] == -3
        assert averages["rounds_played"] == 2

    def test_history_beyond_window_is_ignored(self):
        history = [_round(1, score_to_par=-10)] + [_round(1, score_to_par=0) for _ in range(5)]
        averages = rolling_round_averages(history, window=1)
        assert averages["rounds_played"] == 1
        assert averages["avg_score_to_par"] == -10


class TestBuildRoundEventFeatures:
    def _event(self, participants=None):
        return {
            "event_key": "SPORT#PGA#EVENT#1", "event_date": "2026-08-20",
            "purse": 20000000, "is_major": False,
            "participants": participants or [{"entity_id": "1"}, {"entity_id": "2"}],
        }

    def test_identifier_round_number_and_context_fields(self):
        event = self._event()
        participant = {"entity_id": "1"}
        round_result = _round(2, score_to_par=-3)

        row = build_round_event_features(event, participant, round_result, [], [])

        assert row["event_key"] == "SPORT#PGA#EVENT#1"
        assert row["entity_id"] == "1"
        assert row["round_number"] == 2
        assert row["purse"] == 20000000
        assert row["field_size"] == 2

    def test_label_is_this_rounds_own_score_to_par(self):
        event = self._event()
        round_result = _round(1, score_to_par=-5)

        row = build_round_event_features(event, {"entity_id": "1"}, round_result, [], [])

        assert row["label_round_score_to_par"] == -5

    def test_overall_and_same_round_history_are_independently_prefixed(self):
        event = self._event()
        round_result = _round(1, score_to_par=-1)
        prior_overall = [_result(finish_position=3, score_to_par=-9, earnings=100000)]
        prior_same_round = [_round(1, score_to_par=-7), _round(1, score_to_par=-5)]

        row = build_round_event_features(event, {"entity_id": "1"}, round_result, prior_overall, prior_same_round)

        assert row["overall_avg_score_to_par"] == -9
        assert row["same_round_avg_score_to_par"] == -6

    def test_dirty_non_numeric_stored_values_are_coerced_to_none_not_left_as_strings(self):
        event = self._event()
        event["purse"] = ""
        round_result = _round(1, score_to_par="")

        row = build_round_event_features(event, {"entity_id": "1"}, round_result, [], [])

        assert row["purse"] is None
        assert row["label_round_score_to_par"] is None


class TestBuildCutlineEventFeatures:
    def _event(self, participants=None, cut_score=-2, cut_count=71, purse=9000000, major=False):
        return {
            "event_key": "SPORT#PGA#EVENT#1", "event_date": "2026-07-10",
            "purse": purse, "is_major": major, "cut_score": cut_score, "cut_count": cut_count,
            "participants": participants or [{"entity_id": str(i)} for i in range(150)],
        }

    def test_identifier_and_context_fields(self):
        event = self._event()
        row = build_cutline_event_features(event)
        assert row["event_key"] == "SPORT#PGA#EVENT#1"
        assert row["purse"] == 9000000
        assert row["field_size"] == 150

    def test_label_cut_score_and_cut_count_pass_through(self):
        event = self._event(cut_score=-2, cut_count=71)
        row = build_cutline_event_features(event)
        assert row["label_cut_score"] == -2
        assert row["cut_count"] == 71

    def test_no_cut_event_reports_a_real_zero_not_none(self):
        event = self._event(cut_score=0, cut_count=0)
        row = build_cutline_event_features(event)
        assert row["label_cut_score"] == 0
        assert row["cut_count"] == 0

    def test_course_avg_cut_score_folds_in_prior_course_history(self):
        event = self._event()
        row = build_cutline_event_features(event, prior_course_cut_scores=[-1, -3])
        assert row["course_avg_cut_score"] == -2

    def test_no_course_history_is_none_not_an_error(self):
        event = self._event()
        row = build_cutline_event_features(event)
        assert row["course_avg_cut_score"] is None

    def test_dirty_non_numeric_stored_values_are_coerced_to_none_not_left_as_strings(self):
        event = self._event(cut_score="", cut_count="", purse="")

        row = build_cutline_event_features(event, prior_course_cut_scores=[-1, "", -3])

        assert row["purse"] is None
        assert row["label_cut_score"] is None
        assert row["cut_count"] is None
        assert row["course_avg_cut_score"] == -2  # the dirty entry is dropped, not summed in


def _match_event(match_format="foursome", home_ids=("1", "2"), away_ids=("3", "4"), home_won=True, halved=False):
    return {
        "event_key": "SPORT#PGA#EVENT#401465497-match-10951", "event_date": "2022-09-22",
        "match_format": match_format,
        "participants": [
            {"entity_id": "USA", "role": "home", "golfer_entity_ids": list(home_ids), "result": {"won": home_won, "halved": halved}},
            {"entity_id": "INTL", "role": "away", "golfer_entity_ids": list(away_ids), "result": {"won": (not home_won) and not halved, "halved": halved}},
        ],
    }


class TestBuildMatchEventFeatures:
    def test_identifier_and_context_fields(self):
        row = build_match_event_features(_match_event(), {}, {})
        assert row["event_key"] == "SPORT#PGA#EVENT#401465497-match-10951"
        assert row["match_format"] == "foursome"
        assert row["is_singles"] is False

    def test_singles_flag(self):
        row = build_match_event_features(_match_event(match_format="singles"), {}, {})
        assert row["is_singles"] is True

    def test_label_home_won_true(self):
        row = build_match_event_features(_match_event(home_won=True), {}, {})
        assert row["label_home_won"] is True

    def test_label_home_won_false(self):
        row = build_match_event_features(_match_event(home_won=False), {}, {})
        assert row["label_home_won"] is False

    def test_halved_match_has_no_label(self):
        row = build_match_event_features(_match_event(halved=True), {}, {})
        assert row["label_home_won"] is None

    def test_home_and_away_form_averaged_across_pairing(self):
        home_prior = {
            "1": [_result(finish_position=1, score_to_par=-10)],
            "2": [_result(finish_position=5, score_to_par=-4)],
        }
        away_prior = {"3": [_result(finish_position=20, score_to_par=2)]}
        row = build_match_event_features(_match_event(away_ids=("3",)), home_prior, away_prior)
        assert row["home_avg_score_to_par"] == -7  # mean(-10, -4)
        assert row["away_avg_score_to_par"] == 2

    def test_singles_match_averages_over_a_single_golfer(self):
        home_prior = {"1": [_result(finish_position=1, score_to_par=-10)]}
        row = build_match_event_features(_match_event(match_format="singles", home_ids=("1",)), home_prior, {})
        assert row["home_avg_score_to_par"] == -10

    def test_no_prior_history_is_none_not_an_error(self):
        row = build_match_event_features(_match_event(), {}, {})
        assert row["home_avg_score_to_par"] is None
        assert row["away_avg_score_to_par"] is None


def _cup_event(home_won=True, halved=False):
    return {
        "event_key": "SPORT#PGA#EVENT#401465497", "event_date": "2022-09-22", "tournament_name": "Presidents Cup",
        "participants": [
            {"entity_id": "USA", "role": "home", "result": {"won": home_won, "halved": halved}},
            {"entity_id": "INTL", "role": "away", "result": {"won": (not home_won) and not halved, "halved": halved}},
        ],
    }


class TestBuildCupEventFeatures:
    def test_identifier_and_context_fields(self):
        row = build_cup_event_features(_cup_event(), {}, {})
        assert row["event_key"] == "SPORT#PGA#EVENT#401465497"
        assert row["tournament_name"] == "Presidents Cup"

    def test_label_home_won(self):
        assert build_cup_event_features(_cup_event(home_won=True), {}, {})["label_home_won"] is True
        assert build_cup_event_features(_cup_event(home_won=False), {}, {})["label_home_won"] is False

    def test_halved_cup_has_no_label(self):
        row = build_cup_event_features(_cup_event(halved=True), {}, {})
        assert row["label_home_won"] is None

    def test_form_averaged_across_full_roster(self):
        home_roster = {
            "1": [_result(finish_position=1, score_to_par=-10)],
            "2": [_result(finish_position=5, score_to_par=-4)],
            "3": [_result(finish_position=30, score_to_par=6)],
        }
        row = build_cup_event_features(_cup_event(), home_roster, {})
        assert row["home_avg_score_to_par"] == -8 / 3  # mean(-10, -4, 6)
