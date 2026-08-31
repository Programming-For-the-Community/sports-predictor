"""
Unit tests for library.features.f1_points -- the real F1 points table,
tie-splitting, the fastest-lap bonus, and constructor points-as-a-sum.
"""
from library.features.f1_points import (
    FASTEST_LAP_BONUS,
    add_fastest_lap_bonus,
    constructor_points,
    points_for_field,
)


class TestPointsForField:
    def test_real_race_points_table_positions_1_through_10(self):
        finish_positions = {str(i): i for i in range(1, 12)}
        result = points_for_field(finish_positions)

        assert result["1"] == 25
        assert result["2"] == 18
        assert result["3"] == 15
        assert result["10"] == 1
        assert result["11"] == 0  # outside the scoring table

    def test_a_dnf_with_no_finish_position_scores_zero(self):
        result = points_for_field({"1": 1, "2": None})
        assert result["2"] == 0.0

    def test_a_tie_splits_the_average_of_the_positions_it_spans(self):
        # Two drivers tied for 9th split the average of 9th (2 pts) and
        # 10th (1 pt) = 1.5 each.
        result = points_for_field({"a": 9, "b": 9})
        assert result["a"] == 1.5
        assert result["b"] == 1.5

    def test_sprint_uses_the_smaller_sprint_table(self):
        result = points_for_field({"1": 1}, sprint=True)
        assert result["1"] == 8

    def test_half_points_halves_every_position(self):
        result = points_for_field({"1": 1, "2": 2}, half_points=True)
        assert result["1"] == 12.5
        assert result["2"] == 9.0


class TestAddFastestLapBonus:
    def test_a_top_10_finisher_gets_the_bonus(self):
        points = {"a": 1.0}
        finish_positions = {"a": 10}

        updated = add_fastest_lap_bonus(points, finish_positions, "a")

        assert updated["a"] == 1.0 + FASTEST_LAP_BONUS

    def test_outside_the_top_10_gets_no_bonus(self):
        points = {"a": 0.0}
        finish_positions = {"a": 11}

        updated = add_fastest_lap_bonus(points, finish_positions, "a")

        assert updated["a"] == 0.0

    def test_a_dnf_fastest_lap_setter_gets_no_bonus(self):
        points = {"a": 0.0}
        finish_positions = {"a": None}

        updated = add_fastest_lap_bonus(points, finish_positions, "a")

        assert updated["a"] == 0.0

    def test_no_fastest_lap_entity_id_is_a_no_op(self):
        points = {"a": 5.0}
        assert add_fastest_lap_bonus(points, {"a": 1}, None) == points

    def test_does_not_mutate_the_input_dict(self):
        points = {"a": 1.0}
        add_fastest_lap_bonus(points, {"a": 1}, "a")
        assert points["a"] == 1.0


class TestConstructorPoints:
    def test_sums_both_drivers_points_for_their_shared_constructor(self):
        driver_points = {"max_verstappen": 25.0, "perez": 18.0, "sainz": 15.0}
        driver_to_constructor = {"max_verstappen": "red_bull", "perez": "red_bull", "sainz": "ferrari"}

        totals = constructor_points(driver_points, driver_to_constructor)

        assert totals["red_bull"] == 43.0
        assert totals["ferrari"] == 15.0

    def test_a_driver_with_no_known_constructor_is_skipped_not_crashed(self):
        totals = constructor_points({"unknown_driver": 10.0}, {})
        assert totals == {}
