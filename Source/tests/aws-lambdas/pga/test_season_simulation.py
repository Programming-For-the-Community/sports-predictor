"""
Unit tests for aws-lambdas/pga/predict/season_simulation.py -- the Monte
Carlo core, seeded for determinism throughout.
"""
import random

import season_simulation


class TestSimulateEventScores:
    def test_returns_one_sampled_score_per_golfer_in_field(self):
        rng = random.Random(1)
        result = season_simulation.simulate_event_scores(["a", "b"], {"a": -5.0, "b": 2.0}, rmse=2.0, rng=rng)
        assert set(result) == {"a", "b"}

    def test_scores_are_rounded_to_a_whole_stroke(self):
        rng = random.Random(1)
        result = season_simulation.simulate_event_scores(["a"], {"a": -5.0}, rmse=2.0, rng=rng)
        assert result["a"] == int(result["a"])

    def test_a_golfer_with_no_mu_at_all_is_skipped_not_fabricated(self):
        rng = random.Random(1)
        result = season_simulation.simulate_event_scores(["a", "b"], {"a": -5.0}, rmse=2.0, rng=rng)
        assert set(result) == {"a"}

    def test_same_seed_reproduces_identical_scores(self):
        r1 = season_simulation.simulate_event_scores(["a", "b"], {"a": -5.0, "b": 1.0}, rmse=2.0, rng=random.Random(42))
        r2 = season_simulation.simulate_event_scores(["a", "b"], {"a": -5.0, "b": 1.0}, rmse=2.0, rng=random.Random(42))
        assert r1 == r2


class TestRankField:
    def test_lowest_score_wins_position_1(self):
        positions = season_simulation.rank_field({"a": -8, "b": -3, "c": 1})
        assert positions == {"a": 1, "b": 2, "c": 3}

    def test_tied_scores_share_the_same_position(self):
        positions = season_simulation.rank_field({"a": -5, "b": -5, "c": 0})
        assert positions["a"] == positions["b"] == 1
        assert positions["c"] == 3  # the tie occupies positions 1-2; next real slot is 3

    def test_a_three_way_tie_still_reports_the_same_shared_position(self):
        positions = season_simulation.rank_field({"a": -5, "b": -5, "c": -5, "d": 0})
        assert positions["a"] == positions["b"] == positions["c"] == 1
        assert positions["d"] == 4


class TestTopNByPoints:
    def test_returns_exactly_n_when_no_ties_at_the_cutoff(self):
        points = {str(i): float(100 - i) for i in range(10)}
        result = season_simulation._top_n_by_points(points, 5)
        assert len(result) == 5
        assert set(result) == {"0", "1", "2", "3", "4"}

    def test_a_tie_at_the_cutoff_lets_everyone_tied_there_through(self):
        points = {"a": 10, "b": 8, "c": 5, "d": 5, "e": 1}
        result = season_simulation._top_n_by_points(points, 3)
        # "c" and "d" are tied for the 3rd spot -- both make it, not
        # arbitrarily one of them.
        assert set(result) == {"a", "b", "c", "d"}

    def test_fewer_golfers_than_n_returns_everyone(self):
        points = {"a": 10, "b": 5}
        assert set(season_simulation._top_n_by_points(points, 70)) == {"a", "b"}


class TestSimulateOneIteration:
    def _tiny_setup(self):
        remaining_events = [
            {"tier": "regular", "field": ["a", "b", "c"], "mu": {"a": -10.0, "b": -2.0, "c": 3.0}, "rmse": 1.0},
        ]
        fedex_st_jude = {"mu": {"a": -8.0, "b": -1.0, "c": 4.0}, "rmse": 1.0}
        bmw_championship = {"mu": {"a": -9.0, "b": 0.0, "c": 5.0}, "rmse": 1.0}
        tour_championship_mu = {"a": -12.0, "b": 2.0, "c": 6.0}
        current_points = {"a": 100.0, "b": 50.0, "c": 10.0}
        return remaining_events, fedex_st_jude, bmw_championship, tour_championship_mu, current_points

    def test_produces_a_champion_from_the_tour_championship_field_only(self):
        remaining_events, fedex_st_jude, bmw, tc_mu, current_points = self._tiny_setup()
        result = season_simulation.simulate_one_iteration(
            remaining_events, fedex_st_jude, bmw, tc_mu, 1.0, current_points, random.Random(7),
        )
        assert result["champion"] in result["tour_championship_field"]

    def test_points_accumulate_on_top_of_real_current_points(self):
        remaining_events, fedex_st_jude, bmw, tc_mu, current_points = self._tiny_setup()
        result = season_simulation.simulate_one_iteration(
            remaining_events, fedex_st_jude, bmw, tc_mu, 1.0, current_points, random.Random(7),
        )
        # "a" started with a huge points lead and the best (lowest) mu at
        # every stage -- should end with more than their starting total.
        assert result["points"]["a"] > current_points["a"]

    def test_fields_narrow_at_each_real_playoffs_cutoff(self):
        # With only 3 golfers total, every field-size cutoff (70/50/30)
        # simply includes everyone -- narrowing itself is exercised in
        # TestTopNByPoints; this just confirms the shape/keys exist.
        remaining_events, fedex_st_jude, bmw, tc_mu, current_points = self._tiny_setup()
        result = season_simulation.simulate_one_iteration(
            remaining_events, fedex_st_jude, bmw, tc_mu, 1.0, current_points, random.Random(7),
        )
        assert set(result["fedex_st_jude_field"]) == {"a", "b", "c"}
        assert set(result["bmw_field"]) == {"a", "b", "c"}
        assert set(result["tour_championship_field"]) == {"a", "b", "c"}

    def test_tour_championship_awards_no_points_of_its_own(self):
        remaining_events, fedex_st_jude, bmw, tc_mu, current_points = self._tiny_setup()
        result = season_simulation.simulate_one_iteration(
            remaining_events, fedex_st_jude, bmw, tc_mu, 1.0, current_points, random.Random(7),
        )
        # Re-run with a champion-only mu swap that would change nothing
        # about points -- points total should be identical regardless of
        # who wins TOUR Championship, since it pays no points at all.
        result_2 = season_simulation.simulate_one_iteration(
            remaining_events, fedex_st_jude, bmw, {"a": 5.0, "b": -20.0, "c": -1.0}, 1.0, current_points, random.Random(7),
        )
        assert result["points"] == result_2["points"]

    def test_empty_tour_championship_field_produces_no_champion(self):
        result = season_simulation.simulate_one_iteration(
            [], {"mu": {}, "rmse": 1.0}, {"mu": {}, "rmse": 1.0}, {}, 1.0, {}, random.Random(1),
        )
        assert result["champion"] is None


class TestSimulateSeason:
    def test_probabilities_are_between_0_and_1_and_sum_of_simulations_matches(self):
        remaining_events = [
            {"tier": "regular", "field": ["a", "b"], "mu": {"a": -5.0, "b": 1.0}, "rmse": 2.0},
        ]
        fedex_st_jude = {"mu": {"a": -5.0, "b": 1.0}, "rmse": 2.0}
        bmw = {"mu": {"a": -5.0, "b": 1.0}, "rmse": 2.0}
        tc_mu = {"a": -5.0, "b": 1.0}
        result = season_simulation.simulate_season(
            remaining_events, fedex_st_jude, bmw, tc_mu, 2.0, {"a": 100.0, "b": 50.0},
            simulations=50, seed=3,
        )
        assert result["simulations"] == 50
        for row in result["standings"]:
            for key in ("fedex_st_jude_probability", "bmw_probability", "tour_championship_probability", "champion_probability"):
                assert 0.0 <= row[key] <= 1.0

    def test_standings_are_sorted_by_projected_points_descending(self):
        remaining_events = [
            {"tier": "regular", "field": ["a", "b", "c"], "mu": {"a": -10.0, "b": 0.0, "c": 5.0}, "rmse": 1.0},
        ]
        fedex_st_jude = {"mu": {"a": -10.0, "b": 0.0, "c": 5.0}, "rmse": 1.0}
        bmw = {"mu": {"a": -10.0, "b": 0.0, "c": 5.0}, "rmse": 1.0}
        tc_mu = {"a": -10.0, "b": 0.0, "c": 5.0}
        result = season_simulation.simulate_season(
            remaining_events, fedex_st_jude, bmw, tc_mu, 1.0, {"a": 1000.0, "b": 500.0, "c": 10.0},
            simulations=30, seed=5,
        )
        points = [row["projected_points"] for row in result["standings"]]
        assert points == sorted(points, reverse=True)

    def test_a_golfer_only_appearing_in_the_tour_championship_mu_is_still_included(self):
        result = season_simulation.simulate_season(
            [], {"mu": {}, "rmse": 1.0}, {"mu": {}, "rmse": 1.0}, {"z": -3.0}, 1.0, {},
            simulations=10, seed=1,
        )
        assert any(row["entity_id"] == "z" for row in result["standings"])

    def test_deterministic_given_the_same_seed(self):
        args = (
            [{"tier": "regular", "field": ["a"], "mu": {"a": -5.0}, "rmse": 1.0}],
            {"mu": {"a": -5.0}, "rmse": 1.0}, {"mu": {"a": -5.0}, "rmse": 1.0}, {"a": -5.0}, 1.0, {"a": 10.0},
        )
        r1 = season_simulation.simulate_season(*args, simulations=20, seed=99)
        r2 = season_simulation.simulate_season(*args, simulations=20, seed=99)
        assert r1 == r2
