"""
Unit tests for library.features.f1 -- rolling driver/constructor
averages, the per-(driver, race) training row builder, and the
per-(constructor, race) training row builder.
"""
import pytest

from library.features.f1 import (
    build_constructor_event_features,
    build_driver_event_features,
    build_sprint_event_features,
    rolling_constructor_averages,
    rolling_driver_averages,
    rolling_qualifying_pace,
)


def _result(finish_position=None, grid_position=None, points=0.0, status="finished", qualifying=None):
    return {
        "finish_position": finish_position, "grid_position": grid_position, "points": points, "status": status,
        "qualifying": qualifying,
    }


def _qualifying(position=None, gap_to_pole_seconds=None):
    return {"position": position, "gap_to_pole_seconds": gap_to_pole_seconds}


def _event(event_key="SPORT#F1#EVENT#2024-1", circuit_id="bahrain", participants=None):
    return {"event_key": event_key, "event_date": "2024-03-02", "circuit_id": circuit_id, "participants": participants or [{}]}


class TestRollingDriverAverages:
    def test_empty_history_returns_all_none_and_zero_starts(self):
        result = rolling_driver_averages([])
        assert result["avg_finish_position"] is None
        assert result["podium_rate"] is None
        assert result["starts"] == 0

    def test_averages_only_over_rows_with_a_real_finish_position(self):
        # One classified 2nd, one DNF (no finish_position at all) --
        # avg_finish_position should be exactly 2, not blended with a
        # phantom 0/None.
        history = [_result(finish_position=2), _result(finish_position=None, status="dnf")]

        result = rolling_driver_averages(history)

        assert result["avg_finish_position"] == 2
        assert result["starts"] == 2

    def test_podium_and_dnf_rate_are_divided_by_starts_not_just_finishes(self):
        history = [
            _result(finish_position=1), _result(finish_position=5),
            _result(finish_position=None, status="dnf"), _result(finish_position=None, status="dnf"),
        ]

        result = rolling_driver_averages(history)

        assert result["podium_rate"] == 0.25  # 1 podium / 4 starts
        assert result["dnf_rate"] == 0.5       # 2 dnf / 4 starts

    def test_best_finish_position_is_the_minimum(self):
        history = [_result(finish_position=8), _result(finish_position=2), _result(finish_position=15)]
        assert rolling_driver_averages(history)["best_finish_position"] == 2

    def test_window_caps_how_many_rows_are_averaged(self):
        history = [_result(finish_position=1)] * 3 + [_result(finish_position=20)] * 3
        result = rolling_driver_averages(history, window=3)
        assert result["avg_finish_position"] == 1
        assert result["starts"] == 3

    def test_avg_points_ignores_non_numeric_points(self):
        history = [_result(points=25.0), _result(points=18.0)]
        assert rolling_driver_averages(history)["avg_points"] == 21.5


class TestRollingConstructorAverages:
    def test_pools_both_drivers_results_through_the_same_math(self):
        # Same shape/math as rolling_driver_averages -- this just
        # confirms the pooled-history call reuses it correctly.
        pooled = [_result(finish_position=1), _result(finish_position=2)]
        assert rolling_constructor_averages(pooled) == rolling_driver_averages(pooled)


class TestRollingQualifyingPace:
    def test_empty_history_returns_all_none_and_zero_sessions(self):
        result = rolling_qualifying_pace([])
        assert result["avg_gap_to_pole_seconds"] is None
        assert result["qualifying_sessions"] == 0

    def test_averages_gap_to_pole_and_position(self):
        history = [_qualifying(position=1, gap_to_pole_seconds=0.0), _qualifying(position=3, gap_to_pole_seconds=0.4)]

        result = rolling_qualifying_pace(history)

        assert result["avg_gap_to_pole_seconds"] == pytest.approx(0.2)
        assert result["avg_qualifying_position"] == 2
        assert result["best_qualifying_position"] == 1
        assert result["qualifying_sessions"] == 2

    def test_window_caps_how_many_sessions_are_averaged(self):
        history = [_qualifying(position=1, gap_to_pole_seconds=0.0)] * 2 + [_qualifying(position=20, gap_to_pole_seconds=3.0)] * 2
        result = rolling_qualifying_pace(history, window=2)
        assert result["avg_qualifying_position"] == 1
        assert result["qualifying_sessions"] == 2


class TestBuildDriverEventFeatures:
    def test_a_winner_gets_win_and_podium_labels(self):
        event = _event()
        participant = {"entity_id": "max_verstappen", "constructor_entity_id": "red_bull", "result": _result(finish_position=1, grid_position=1)}

        row = build_driver_event_features(event, participant, prior_results=[])

        assert row["entity_id"] == "max_verstappen"
        assert row["constructor_entity_id"] == "red_bull"
        assert row["circuit_id"] == "bahrain"
        assert row["grid_position"] == 1
        assert row["label_win"] == 1
        assert row["label_podium"] == 1
        assert row["label_finish_position"] == 1
        assert row["label_dnf"] == 0

    def test_a_dnf_gets_no_real_finish_position_label_but_is_flagged(self):
        event = _event()
        participant = {"entity_id": "d", "result": _result(finish_position=None, status="dnf")}

        row = build_driver_event_features(event, participant, prior_results=[])

        assert row["label_finish_position"] is None
        assert row["label_dnf"] == 1
        assert row["label_win"] == 0
        assert row["label_podium"] == 0

    def test_prior_results_feed_the_unprefixed_rolling_block(self):
        event = _event()
        participant = {"entity_id": "d", "result": _result(finish_position=5)}
        prior = [_result(finish_position=1), _result(finish_position=2)]

        row = build_driver_event_features(event, participant, prior_results=prior)

        assert row["avg_finish_position"] == 1.5
        assert row["starts"] == 2

    def test_circuit_results_feed_the_circuit_prefixed_block(self):
        event = _event()
        participant = {"entity_id": "d", "result": _result(finish_position=5)}
        circuit_history = [_result(finish_position=3)]

        row = build_driver_event_features(event, participant, prior_results=[], circuit_results=circuit_history)

        assert row["circuit_avg_finish_position"] == 3
        assert row["circuit_starts"] == 1

    def test_missing_circuit_and_constructor_history_still_produces_every_column(self):
        event = _event()
        participant = {"entity_id": "d", "result": _result(finish_position=5)}

        row = build_driver_event_features(event, participant, prior_results=[])

        assert row["circuit_avg_finish_position"] is None
        assert row["constructor_avg_finish_position"] is None

    def test_constructor_results_feed_the_constructor_prefixed_block(self):
        event = _event()
        participant = {"entity_id": "d", "result": _result(finish_position=5)}
        constructor_history = [_result(finish_position=1), _result(finish_position=1)]

        row = build_driver_event_features(event, participant, prior_results=[], constructor_results=constructor_history)

        assert row["constructor_avg_finish_position"] == 1

    def test_field_size_counts_this_events_own_participants(self):
        event = _event(participants=[{}, {}, {}])
        participant = {"entity_id": "d", "result": _result(finish_position=1)}

        row = build_driver_event_features(event, participant, prior_results=[])

        assert row["field_size"] == 3

    def test_label_qualifying_position_comes_from_the_merged_qualifying_result(self):
        event = _event()
        participant = {"entity_id": "d", "result": _result(finish_position=5, qualifying=_qualifying(position=3))}

        row = build_driver_event_features(event, participant, prior_results=[])

        assert row["label_qualifying_position"] == 3

    def test_label_qualifying_position_is_none_when_qualifying_was_never_merged(self):
        event = _event()
        participant = {"entity_id": "d", "result": _result(finish_position=5)}  # qualifying=None by default

        row = build_driver_event_features(event, participant, prior_results=[])

        assert row["label_qualifying_position"] is None

    def test_qualifying_history_feeds_the_qualifying_prefixed_block(self):
        event = _event()
        participant = {"entity_id": "d", "result": _result(finish_position=5)}
        qualifying_history = [_qualifying(position=1, gap_to_pole_seconds=0.0)]

        row = build_driver_event_features(event, participant, prior_results=[], qualifying_history=qualifying_history)

        assert row["qualifying_avg_qualifying_position"] == 1
        assert row["qualifying_avg_gap_to_pole_seconds"] == 0.0

    def test_constructor_qualifying_history_feeds_its_own_prefixed_block(self):
        event = _event()
        participant = {"entity_id": "d", "result": _result(finish_position=5)}
        pooled = [_qualifying(position=1, gap_to_pole_seconds=0.0), _qualifying(position=4, gap_to_pole_seconds=0.3)]

        row = build_driver_event_features(event, participant, prior_results=[], constructor_qualifying_history=pooled)

        assert row["constructor_qualifying_avg_qualifying_position"] == 2.5

    def test_missing_qualifying_history_still_produces_every_column(self):
        event = _event()
        participant = {"entity_id": "d", "result": _result(finish_position=5)}

        row = build_driver_event_features(event, participant, prior_results=[])

        assert row["qualifying_avg_gap_to_pole_seconds"] is None
        assert row["constructor_qualifying_avg_gap_to_pole_seconds"] is None


class TestBuildConstructorEventFeatures:
    def test_wins_if_either_driver_finished_first(self):
        event = _event()
        participants = [
            {"entity_id": "a", "result": _result(finish_position=1)},
            {"entity_id": "b", "result": _result(finish_position=8)},
        ]

        row = build_constructor_event_features(event, "red_bull", participants, prior_results_by_driver={})

        assert row["label_win"] == 1
        assert row["entity_id"] == "red_bull"

    def test_no_win_if_neither_driver_finished_first(self):
        event = _event()
        participants = [
            {"entity_id": "a", "result": _result(finish_position=2)},
            {"entity_id": "b", "result": _result(finish_position=8)},
        ]

        row = build_constructor_event_features(event, "ferrari", participants, prior_results_by_driver={})

        assert row["label_win"] == 0

    def test_a_double_dnf_with_no_finish_positions_is_not_a_win(self):
        event = _event()
        participants = [
            {"entity_id": "a", "result": _result(finish_position=None, status="dnf")},
            {"entity_id": "b", "result": _result(finish_position=None, status="dnf")},
        ]

        row = build_constructor_event_features(event, "team", participants, prior_results_by_driver={})

        assert row["label_win"] == 0

    def test_both_drivers_rolling_form_is_summed_not_averaged(self):
        event = _event()
        participants = [{"entity_id": "a", "result": _result(finish_position=5)}, {"entity_id": "b", "result": _result(finish_position=6)}]
        prior_by_driver = {
            "a": [_result(points=25.0)],
            "b": [_result(points=18.0)],
        }

        row = build_constructor_event_features(event, "red_bull", participants, prior_results_by_driver=prior_by_driver)

        assert row["avg_points"] == 43.0  # sum, not the mean (21.5)
        assert row["starts"] == 2         # 1 start each, summed

    def test_a_driver_with_no_prior_history_still_contributes_the_others_value_in_full(self):
        event = _event()
        participants = [{"entity_id": "a", "result": _result(finish_position=1)}, {"entity_id": "rookie", "result": _result(finish_position=10)}]
        prior_by_driver = {"a": [_result(points=25.0)]}  # rookie has no entry at all

        row = build_constructor_event_features(event, "team", participants, prior_results_by_driver=prior_by_driver)

        assert row["avg_points"] == 25.0


class TestBuildSprintEventFeatures:
    def test_a_winner_gets_win_and_podium_labels(self):
        event = _event()
        participant = {"entity_id": "d", "constructor_entity_id": "red_bull", "result": _result(finish_position=1, grid_position=2)}

        row = build_sprint_event_features(event, participant, prior_sprint_results=[])

        assert row["label_win"] == 1
        assert row["label_podium"] == 1
        assert row["label_dnf"] == 0

    def test_label_sprint_grid_position_comes_from_the_sprints_own_grid_field(self):
        event = _event()
        participant = {"entity_id": "d", "result": _result(finish_position=5, grid_position=4)}

        row = build_sprint_event_features(event, participant, prior_sprint_results=[])

        assert row["label_sprint_grid_position"] == 4

    def test_prior_sprint_results_feed_the_unprefixed_rolling_block_separately_from_main_race(self):
        event = _event()
        participant = {"entity_id": "d", "result": _result(finish_position=5)}
        prior_sprint = [_result(finish_position=1)]

        row = build_sprint_event_features(event, participant, prior_sprint_results=prior_sprint)

        assert row["avg_finish_position"] == 1
        assert row["starts"] == 1

    def test_no_circuit_fit_or_qualifying_rolling_columns_are_produced(self):
        # circuit_id itself IS still a plain context column (this
        # Sprint's own circuit) -- only the circuit_fit ROLLING block
        # (circuit_avg_finish_position etc., a prefix build_driver_
        # event_features produces from a distinct circuit_results arg
        # this function has no equivalent parameter for) is absent.
        event = _event()
        participant = {"entity_id": "d", "result": _result(finish_position=5)}

        row = build_sprint_event_features(event, participant, prior_sprint_results=[])

        assert row["circuit_id"] == "bahrain"
        assert "circuit_avg_finish_position" not in row
        assert not any(key.startswith("qualifying_") for key in row)

    def test_constructor_sprint_results_feed_the_constructor_prefixed_block(self):
        event = _event()
        participant = {"entity_id": "d", "result": _result(finish_position=5)}
        constructor_sprint = [_result(finish_position=1), _result(finish_position=1)]

        row = build_sprint_event_features(event, participant, prior_sprint_results=[], constructor_sprint_results=constructor_sprint)

        assert row["constructor_avg_finish_position"] == 1
