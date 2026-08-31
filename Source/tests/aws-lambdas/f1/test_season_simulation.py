"""
Unit tests for aws-lambdas/f1/predict/season_simulation.py -- the Monte
Carlo core, seeded for determinism throughout.
"""
import random

import season_simulation


class TestSimulateDnfs:
    def test_a_driver_with_no_known_probability_defaults_to_never_dnf(self):
        rng = random.Random(1)
        dnfs = season_simulation.simulate_dnfs(["a"], {}, rng)
        assert dnfs == set()

    def test_a_certain_dnf_probability_always_dnfs(self):
        rng = random.Random(1)
        dnfs = season_simulation.simulate_dnfs(["a"], {"a": 1.0}, rng)
        assert dnfs == {"a"}

    def test_same_seed_reproduces_identical_dnfs(self):
        d1 = season_simulation.simulate_dnfs(["a", "b", "c"], {"a": 0.5, "b": 0.5, "c": 0.5}, random.Random(42))
        d2 = season_simulation.simulate_dnfs(["a", "b", "c"], {"a": 0.5, "b": 0.5, "c": 0.5}, random.Random(42))
        assert d1 == d2


class TestSimulateRaceFinishPositions:
    def test_lowest_sampled_value_wins_position_1(self):
        rng = random.Random(1)
        positions = season_simulation.simulate_race_finish_positions(
            ["a", "b", "c"], {"a": 1.0, "b": 10.0, "c": 20.0}, rmse=0.01, dnfs=set(), rng=rng,
        )
        assert positions == {"a": 1, "b": 2, "c": 3}

    def test_a_driver_with_no_mu_at_all_still_appears_but_maps_to_none(self):
        rng = random.Random(1)
        positions = season_simulation.simulate_race_finish_positions(
            ["a", "b"], {"a": 1.0}, rmse=2.0, dnfs=set(), rng=rng,
        )
        assert positions["a"] == 1
        assert positions["b"] is None

    def test_a_dnf_driver_still_appears_but_maps_to_none(self):
        rng = random.Random(1)
        positions = season_simulation.simulate_race_finish_positions(
            ["a", "b"], {"a": 1.0, "b": 2.0}, rmse=0.01, dnfs={"b"}, rng=rng,
        )
        assert positions["a"] == 1
        assert positions["b"] is None


class TestSimulateOneIteration:
    def _race(self, field, mu, dnf_probability=None, sprint=False):
        return {"field": field, "mu": mu, "rmse": 0.01, "dnf_probability": dnf_probability or {}, "sprint": sprint}

    def test_a_win_adds_25_points_to_the_driver_and_their_constructor(self):
        rng = random.Random(1)
        race = self._race(["a", "b"], {"a": 1.0, "b": 20.0})

        result = season_simulation.simulate_one_iteration([race], {"a": "red_bull", "b": "ferrari"}, {}, rng)

        assert result["driver_points"]["a"] == 25.0
        assert result["constructor_points"]["red_bull"] == 25.0
        assert result["champion"] == "a"
        assert result["constructor_champion"] == "red_bull"

    def test_current_points_carry_forward_into_the_simulated_total(self):
        rng = random.Random(1)
        race = self._race(["a", "b"], {"a": 1.0, "b": 20.0})

        result = season_simulation.simulate_one_iteration(
            [race], {"a": "red_bull", "b": "ferrari"}, {"a": 100.0}, rng,
        )

        assert result["driver_points"]["a"] == 125.0  # 100 current + 25 this race

    def test_teammates_points_are_summed_into_one_constructor_total(self):
        rng = random.Random(1)
        # Both drivers on the same constructor, finishing 1st and 2nd.
        race = self._race(["a", "b", "c"], {"a": 1.0, "b": 2.0, "c": 20.0})

        result = season_simulation.simulate_one_iteration(
            [race], {"a": "red_bull", "b": "red_bull", "c": "ferrari"}, {}, rng,
        )

        assert result["constructor_points"]["red_bull"] == 25.0 + 18.0

    def test_a_certain_dnf_scores_no_points_for_that_driver(self):
        rng = random.Random(1)
        race = self._race(["a", "b"], {"a": 1.0, "b": 20.0}, dnf_probability={"a": 1.0})

        result = season_simulation.simulate_one_iteration([race], {"a": "red_bull", "b": "ferrari"}, {}, rng)

        assert result["driver_points"]["a"] == 0.0
        assert result["driver_points"]["b"] == 25.0  # b now wins since a DNF'd

    def test_no_remaining_races_just_returns_current_points_unchanged(self):
        rng = random.Random(1)

        result = season_simulation.simulate_one_iteration([], {"a": "red_bull"}, {"a": 50.0}, rng)

        assert result["driver_points"] == {"a": 50.0}
        assert result["constructor_points"]["red_bull"] == 50.0
        assert result["champion"] == "a"


class TestSimulateSeason:
    def test_a_deterministic_seed_reproduces_identical_output(self):
        race = {"field": ["a", "b"], "mu": {"a": 1.0, "b": 20.0}, "rmse": 2.0, "dnf_probability": {}}
        r1 = season_simulation.simulate_season([race], {"a": "red_bull", "b": "ferrari"}, {}, simulations=20, seed=7)
        r2 = season_simulation.simulate_season([race], {"a": "red_bull", "b": "ferrari"}, {}, simulations=20, seed=7)
        assert r1 == r2

    def test_a_driver_who_always_finishes_first_has_a_100_percent_champion_probability(self):
        race = {"field": ["a", "b"], "mu": {"a": -100.0, "b": 100.0}, "rmse": 0.01, "dnf_probability": {}}

        result = season_simulation.simulate_season([race], {"a": "red_bull", "b": "ferrari"}, {}, simulations=25, seed=1)

        driver_a = next(s for s in result["driver_standings"] if s["entity_id"] == "a")
        assert driver_a["champion_probability"] == 1.0

    def test_produces_both_driver_and_constructor_standings(self):
        race = {"field": ["a", "b"], "mu": {"a": 1.0, "b": 20.0}, "rmse": 2.0, "dnf_probability": {}}

        result = season_simulation.simulate_season(
            [race], {"a": "red_bull", "b": "ferrari"}, {"a": 10.0}, simulations=10, seed=1,
        )

        assert {s["entity_id"] for s in result["driver_standings"]} == {"a", "b"}
        assert {s["entity_id"] for s in result["constructor_standings"]} == {"red_bull", "ferrari"}
        driver_a = next(s for s in result["driver_standings"] if s["entity_id"] == "a")
        assert driver_a["current_points"] == 10.0

    def test_standings_are_sorted_by_projected_points_descending(self):
        race = {"field": ["a", "b", "c"], "mu": {"a": -50.0, "b": 0.0, "c": 50.0}, "rmse": 0.01, "dnf_probability": {}}

        result = season_simulation.simulate_season(
            [race], {"a": "t1", "b": "t2", "c": "t3"}, {}, simulations=10, seed=1,
        )

        points = [s["projected_points"] for s in result["driver_standings"]]
        assert points == sorted(points, reverse=True)
