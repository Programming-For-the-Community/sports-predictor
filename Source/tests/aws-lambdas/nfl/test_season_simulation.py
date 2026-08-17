"""
Unit tests for the Season tab's Monte Carlo simulation and leaderboard
projection. No AWS involved -- every function here is pure, same
testability philosophy as tests/library/features/test_nfl.py. Private
helpers (_seed_conference, _simulate_bracket) are tested directly with
small custom division setups, matching how this repo already tests e.g.
nfl.py's _identify_leader/_mov_multiplier directly rather than only
through their public callers.
"""
import random

import pytest

import season_simulation


class TestSeedConference:
    def test_division_winner_is_the_best_record_in_each_division(self):
        division_teams = {"East": ["e1", "e2"], "West": ["w1", "w2"]}
        wins = {"e1": 10, "e2": 4, "w1": 3, "w2": 9}

        seeds, division_winners = season_simulation._seed_conference(division_teams, wins, {})

        assert division_winners == {"e1", "w2"}

    def test_seeds_ordered_by_record_descending(self):
        division_teams = {"East": ["e1", "e2"], "West": ["w1", "w2"]}
        wins = {"e1": 10, "e2": 8, "w1": 3, "w2": 9}

        seeds, _ = season_simulation._seed_conference(division_teams, wins, {})

        # Division winners: e1 (10) and w2 (9) -- seeded by record.
        assert seeds[0] == "e1"
        assert seeds[1] == "w2"

    def test_wildcards_fill_remaining_seeds_from_non_division_winners(self):
        division_teams = {"East": ["e1", "e2"], "West": ["w1", "w2"]}
        wins = {"e1": 10, "e2": 8, "w1": 3, "w2": 9}

        seeds, division_winners = season_simulation._seed_conference(division_teams, wins, {})

        wildcards = [team for team in seeds if team not in division_winners]
        assert wildcards == ["e2", "w1"]  # only 2 non-division-winners exist here

    def test_ties_broken_by_point_differential(self):
        division_teams = {"East": ["e1", "e2"]}
        wins = {"e1": 10, "e2": 10}
        point_differential = {"e1": 20, "e2": -5}

        _, division_winners = season_simulation._seed_conference(division_teams, wins, point_differential)

        assert division_winners == {"e1"}


class TestSimulateBracket:
    def test_returns_one_of_the_seven_seeds(self):
        seeds = ["s1", "s2", "s3", "s4", "s5", "s6", "s7"]
        ratings = {team: 1500 for team in seeds}

        champion = season_simulation._simulate_bracket(seeds, ratings, home_advantage=55, rng=random.Random(42))

        assert champion in seeds

    def test_a_much_stronger_top_seed_wins_far_more_often_than_a_coin_flip(self):
        seeds = ["s1", "s2", "s3", "s4", "s5", "s6", "s7"]
        ratings = {"s1": 2200}  # everyone else defaults to DEFAULT_STARTING_RATING (1500)
        rng = random.Random(1)

        championships = sum(
            1 for _ in range(500)
            if season_simulation._simulate_bracket(seeds, ratings, home_advantage=55, rng=rng) == "s1"
        )

        # A generic 1-in-7 baseline is ~14% -- a 700-point Elo edge should
        # push this dramatically higher.
        assert championships / 500 > 0.5


class TestProjectBracket:
    SEEDS = ["s1", "s2", "s3", "s4", "s5", "s6", "s7"]

    def test_returns_three_rounds_with_the_right_matchup_counts(self):
        ratings = {team: 1500 for team in self.SEEDS}

        result = season_simulation.project_bracket(self.SEEDS, ratings, home_advantage=55)

        rounds = {r["round"]: r["matchups"] for r in result["rounds"]}
        assert len(rounds["Wild Card"]) == 3
        assert len(rounds["Divisional"]) == 2
        assert len(rounds["Conference Championship"]) == 1
        assert result["champion"] in self.SEEDS

    def test_no_rng_needed_and_deterministic_across_calls(self):
        ratings = {"s1": 1700, "s2": 1650, "s3": 1600, "s4": 1550, "s5": 1500, "s6": 1450, "s7": 1400}

        first = season_simulation.project_bracket(self.SEEDS, ratings, home_advantage=55)
        second = season_simulation.project_bracket(self.SEEDS, ratings, home_advantage=55)

        assert first == second

    def test_a_much_stronger_top_seed_is_favored_to_win_it_all(self):
        ratings = {"s1": 2200}  # everyone else defaults to DEFAULT_STARTING_RATING (1500)

        result = season_simulation.project_bracket(self.SEEDS, ratings, home_advantage=55)

        assert result["champion"] == "s1"

    def test_wild_card_matchups_pair_two_with_seven_three_with_six_four_with_five(self):
        ratings = {team: 1500 for team in self.SEEDS}

        result = season_simulation.project_bracket(self.SEEDS, ratings, home_advantage=55)

        wild_card_pairs = {frozenset((m["team_a"], m["team_b"])) for m in result["rounds"][0]["matchups"]}
        assert wild_card_pairs == {frozenset(("s2", "s7")), frozenset(("s3", "s6")), frozenset(("s4", "s5"))}

    def test_predicted_winners_win_probability_is_always_at_least_half(self):
        ratings = {"s1": 1900, "s2": 1300, "s3": 1600, "s4": 1550, "s5": 1500, "s6": 1450, "s7": 1400}

        result = season_simulation.project_bracket(self.SEEDS, ratings, home_advantage=55)

        for round_ in result["rounds"]:
            for matchup in round_["matchups"]:
                assert matchup["win_probability"] >= 0.5
                assert matchup["predicted_winner"] in (matchup["team_a"], matchup["team_b"])


class TestProjectFullBracket:
    def test_returns_both_conferences_a_super_bowl_pick_and_an_overall_champion(self):
        conference_seeds = {
            "AFC": ["a1", "a2", "a3", "a4", "a5", "a6", "a7"],
            "NFC": ["n1", "n2", "n3", "n4", "n5", "n6", "n7"],
        }
        ratings = {team: 1500 for conference in conference_seeds.values() for team in conference}

        result = season_simulation.project_full_bracket(conference_seeds, ratings, home_advantage=55)

        assert set(result["conferences"]) == {"AFC", "NFC"}
        all_teams = {team for conference in conference_seeds.values() for team in conference}
        assert result["champion"] in all_teams
        assert result["super_bowl"]["predicted_winner"] == result["champion"]
        assert result["super_bowl"]["seed_a"] is None
        assert result["super_bowl"]["seed_b"] is None

    def test_the_stronger_conference_champion_is_favored_in_the_super_bowl(self):
        conference_seeds = {
            "AFC": ["a1", "a2", "a3", "a4", "a5", "a6", "a7"],
            "NFC": ["n1", "n2", "n3", "n4", "n5", "n6", "n7"],
        }
        ratings = {team: 1500 for conference in conference_seeds.values() for team in conference}
        ratings["a1"] = 2400  # dominant enough to win its own conference AND the Super Bowl

        result = season_simulation.project_full_bracket(conference_seeds, ratings, home_advantage=55)

        assert result["champion"] == "a1"


class TestSimulateSeason:
    # Real ESPN team_ids from library.features.nfl_teams.TEAM_DIVISIONS --
    # _divisions_by_conference() reads the real table, so a meaningful
    # end-to-end test needs real ids to land in real divisions/conferences.
    STRONG_TEAM = "12"  # KC, AFC West
    WEAK_TEAM = "13"  # LV, AFC West

    def test_a_much_stronger_team_projects_more_wins_than_a_much_weaker_one(self):
        current_wins = {self.STRONG_TEAM: 5, self.WEAK_TEAM: 5}
        current_losses = {self.STRONG_TEAM: 0, self.WEAK_TEAM: 0}
        remaining_games = [(self.STRONG_TEAM, self.WEAK_TEAM), (self.WEAK_TEAM, self.STRONG_TEAM)]
        current_ratings = {self.STRONG_TEAM: 1900, self.WEAK_TEAM: 1300}

        result = season_simulation.simulate_season(
            current_wins, current_losses, {}, remaining_games, current_ratings,
            simulations=200, rng=random.Random(7),
        )

        assert result[self.STRONG_TEAM]["projected_wins"] > result[self.WEAK_TEAM]["projected_wins"]

    def test_projected_wins_and_losses_sum_to_games_played(self):
        # 2 remaining games each (see remaining_games below) on top of an
        # 0-0 start -- every simulated season path plays exactly 2 games
        # per team, so wins+losses must land on exactly 2 every time,
        # regardless of which side of each game a team happens to win.
        current_wins = {self.STRONG_TEAM: 0, self.WEAK_TEAM: 0}
        current_losses = {self.STRONG_TEAM: 0, self.WEAK_TEAM: 0}
        remaining_games = [(self.STRONG_TEAM, self.WEAK_TEAM), (self.WEAK_TEAM, self.STRONG_TEAM)]
        current_ratings = {self.STRONG_TEAM: 1900, self.WEAK_TEAM: 1300}

        result = season_simulation.simulate_season(
            current_wins, current_losses, {}, remaining_games, current_ratings,
            simulations=200, rng=random.Random(7),
        )

        for team_id in (self.STRONG_TEAM, self.WEAK_TEAM):
            total = result[team_id]["projected_wins"] + result[team_id]["projected_losses"]
            assert total == pytest.approx(2.0)

    def test_probabilities_are_fractions_between_zero_and_one(self):
        current_wins = {self.STRONG_TEAM: 5, self.WEAK_TEAM: 5}
        current_losses = {self.STRONG_TEAM: 0, self.WEAK_TEAM: 0}
        remaining_games = [(self.STRONG_TEAM, self.WEAK_TEAM)]
        current_ratings = {self.STRONG_TEAM: 1600, self.WEAK_TEAM: 1500}

        result = season_simulation.simulate_season(
            current_wins, current_losses, {}, remaining_games, current_ratings,
            simulations=50, rng=random.Random(3),
        )

        for team_projection in result.values():
            for key in ("division_winner_probability", "playoff_probability", "championship_probability"):
                assert 0.0 <= team_projection[key] <= 1.0

    def test_same_seed_is_deterministic(self):
        current_wins = {self.STRONG_TEAM: 5, self.WEAK_TEAM: 5}
        current_losses = {self.STRONG_TEAM: 0, self.WEAK_TEAM: 0}
        remaining_games = [(self.STRONG_TEAM, self.WEAK_TEAM)]
        current_ratings = {self.STRONG_TEAM: 1600, self.WEAK_TEAM: 1500}

        first = season_simulation.simulate_season(
            current_wins, current_losses, {}, remaining_games, current_ratings,
            simulations=50, rng=random.Random(99),
        )
        second = season_simulation.simulate_season(
            current_wins, current_losses, {}, remaining_games, current_ratings,
            simulations=50, rng=random.Random(99),
        )

        assert first == second


class TestProjectLeaderboard:
    def test_projects_current_plus_per_game_times_remaining(self):
        result = season_simulation.project_leaderboard(
            current_totals={"p1": 800.0},
            per_game_projections={"p1": 250.0},
            games_remaining={"p1": 4},
            top_n=10,
        )

        assert result[0]["projected_total"] == pytest.approx(800.0 + 250.0 * 4)

    def test_sorted_descending_and_capped_at_top_n(self):
        current_totals = {"p1": 100.0, "p2": 900.0, "p3": 500.0}
        per_game_projections = {"p1": 0.0, "p2": 0.0, "p3": 0.0}
        games_remaining = {"p1": 0, "p2": 0, "p3": 0}

        result = season_simulation.project_leaderboard(
            current_totals, per_game_projections, games_remaining, top_n=2,
        )

        assert [row["entity_id"] for row in result] == ["p2", "p3"]

    def test_missing_projection_or_games_remaining_defaults_to_no_growth(self):
        result = season_simulation.project_leaderboard(
            current_totals={"p1": 100.0}, per_game_projections={}, games_remaining={}, top_n=10,
        )

        assert result[0]["projected_total"] == 100.0
