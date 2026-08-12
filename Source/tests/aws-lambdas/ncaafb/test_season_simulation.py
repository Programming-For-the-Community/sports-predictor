"""
Unit tests for the NCAAFB Season tab's Monte Carlo simulation and
leaderboard projection. No AWS involved -- every function here is pure,
same testability philosophy as tests/library/features/test_ncaafb.py.
Private helpers (_group_by_conference, _conference_champion,
_select_cfp_field, _simulate_cfp_bracket) are tested directly with small
custom conference setups, same convention
tests/aws-lambdas/nfl/test_season_simulation.py's own docstring
describes for its own private helpers.
"""
import random

import pytest

import season_simulation


class TestGroupByConference:
    def test_groups_teams_by_their_conference(self):
        team_conference = {"a": "X", "b": "Y", "c": "X"}

        result = season_simulation._group_by_conference(team_conference)

        assert result == {"X": ["a", "c"], "Y": ["b"]}

    def test_a_falsy_conference_is_excluded(self):
        team_conference = {"a": "X", "b": None, "c": ""}

        result = season_simulation._group_by_conference(team_conference)

        assert result == {"X": ["a"]}


class TestConferenceChampion:
    def test_best_record_wins(self):
        assert season_simulation._conference_champion(["a", "b"], {"a": 10, "b": 4}, {}) == "a"

    def test_ties_broken_by_point_differential(self):
        members = ["a", "b"]
        wins = {"a": 10, "b": 10}
        point_differential = {"a": -5, "b": 20}

        assert season_simulation._conference_champion(members, wins, point_differential) == "b"


class TestSelectCfpField:
    # 6 conference champions (a1 best by score, f1 worst) plus 14 other
    # ranked teams -- exceeds CFP_AUTO_BIDS (5), so exactly one champion
    # (f1, the worst-ranked) should miss the field entirely, and the
    # 5th-best champion should land in the general seeding pool
    # alongside the at-large teams rather than getting a bye.
    MODEL_SCORES = {
        "e1": 1, "b1": 2, "r3": 3, "r4": 4, "a1": 5, "r6": 6, "r7": 7, "d1": 8,
        "r9": 9, "r10": 10, "r11": 11, "r12": 12, "r13": 13, "r14": 14, "c1": 15,
        "r16": 16, "r17": 17, "r18": 18, "r19": 19, "f1": 20,
    }
    CHAMPIONS = {"ConfA": "a1", "ConfB": "b1", "ConfC": "c1", "ConfD": "d1", "ConfE": "e1", "ConfF": "f1"}

    def test_returns_exactly_twelve_seeds(self):
        seeds = season_simulation._select_cfp_field(self.MODEL_SCORES, self.CHAMPIONS)

        assert len(seeds) == 12
        assert len(set(seeds)) == 12

    def test_four_highest_ranked_champions_get_the_top_four_bye_seeds(self):
        seeds = season_simulation._select_cfp_field(self.MODEL_SCORES, self.CHAMPIONS)

        assert seeds[:4] == ["e1", "b1", "a1", "d1"]

    def test_fifth_ranked_champion_gets_an_auto_bid_but_no_bye(self):
        seeds = season_simulation._select_cfp_field(self.MODEL_SCORES, self.CHAMPIONS)

        assert "c1" in seeds[4:]

    def test_sixth_ranked_champion_misses_the_field_entirely(self):
        seeds = season_simulation._select_cfp_field(self.MODEL_SCORES, self.CHAMPIONS)

        assert "f1" not in seeds

    def test_at_large_seeds_are_the_best_remaining_teams_by_score(self):
        seeds = season_simulation._select_cfp_field(self.MODEL_SCORES, self.CHAMPIONS)

        assert set(seeds[4:]) == {"c1", "r3", "r4", "r6", "r7", "r9", "r10", "r11"}


class TestSimulateCfpBracket:
    SEEDS = [f"s{n}" for n in range(1, 13)]

    def test_returns_one_of_the_twelve_seeds(self):
        ratings = {team: 1500 for team in self.SEEDS}

        champion = season_simulation._simulate_cfp_bracket(self.SEEDS, ratings, home_advantage=55, rng=random.Random(42))

        assert champion in self.SEEDS

    def test_a_much_stronger_top_seed_wins_far_more_often_than_a_naive_baseline(self):
        ratings = {"s1": 2200}  # everyone else defaults to DEFAULT_STARTING_RATING (1500)
        rng = random.Random(1)

        championships = sum(
            1 for _ in range(300)
            if season_simulation._simulate_cfp_bracket(self.SEEDS, ratings, home_advantage=55, rng=rng) == "s1"
        )

        # A generic 1-in-12 baseline is ~8% -- a 700-point Elo edge (plus
        # a bye) should push this dramatically higher.
        assert championships / 300 > 0.35


class TestSimulateSeason:
    TEAMS = [f"t{i}" for i in range(12)]
    TEAM_CONFERENCE = {team: f"C{i // 4}" for i, team in enumerate(TEAMS)}
    STRONG_TEAM = "t0"
    WEAK_TEAM = "t1"

    @staticmethod
    def _score_teams(wins, losses, ratings):
        # More wins -> a lower (better) score, so the CFP-field machinery
        # has something meaningful to sort by without depending on a real
        # model in these pure-function tests.
        return {team: -wins.get(team, 0) for team in TestSimulateSeason.TEAMS}

    def test_a_much_stronger_team_projects_more_wins_than_a_much_weaker_one(self):
        current_wins = {self.STRONG_TEAM: 5, self.WEAK_TEAM: 5}
        current_losses = {self.STRONG_TEAM: 0, self.WEAK_TEAM: 0}
        remaining_games = [(self.STRONG_TEAM, self.WEAK_TEAM), (self.WEAK_TEAM, self.STRONG_TEAM)]
        current_ratings = {self.STRONG_TEAM: 1900, self.WEAK_TEAM: 1300}

        result = season_simulation.simulate_season(
            current_wins, current_losses, {}, remaining_games, current_ratings,
            self.TEAM_CONFERENCE, self._score_teams, simulations=200, rng=random.Random(7),
        )

        assert result[self.STRONG_TEAM]["projected_wins"] > result[self.WEAK_TEAM]["projected_wins"]

    def test_projected_wins_and_losses_sum_to_games_played(self):
        current_wins = {self.STRONG_TEAM: 0, self.WEAK_TEAM: 0}
        current_losses = {self.STRONG_TEAM: 0, self.WEAK_TEAM: 0}
        remaining_games = [(self.STRONG_TEAM, self.WEAK_TEAM), (self.WEAK_TEAM, self.STRONG_TEAM)]
        current_ratings = {self.STRONG_TEAM: 1900, self.WEAK_TEAM: 1300}

        result = season_simulation.simulate_season(
            current_wins, current_losses, {}, remaining_games, current_ratings,
            self.TEAM_CONFERENCE, self._score_teams, simulations=200, rng=random.Random(7),
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
            self.TEAM_CONFERENCE, self._score_teams, simulations=50, rng=random.Random(3),
        )

        for team_projection in result.values():
            for key in ("conference_champion_probability", "bowl_probability", "playoff_probability", "championship_probability"):
                assert 0.0 <= team_projection[key] <= 1.0

    def test_a_team_already_past_the_bowl_bar_with_no_games_left_is_certain(self):
        current_wins = {team: 6 for team in self.TEAMS}
        current_losses = {team: 0 for team in self.TEAMS}
        current_ratings = {team: 1500 for team in self.TEAMS}

        result = season_simulation.simulate_season(
            current_wins, current_losses, {}, [], current_ratings,
            self.TEAM_CONFERENCE, self._score_teams, simulations=10, rng=random.Random(1),
        )

        assert result[self.STRONG_TEAM]["bowl_probability"] == 1.0

    def test_a_team_short_of_the_bowl_bar_with_no_games_left_is_zero(self):
        current_wins = {team: 2 for team in self.TEAMS}
        current_losses = {team: 4 for team in self.TEAMS}
        current_ratings = {team: 1500 for team in self.TEAMS}

        result = season_simulation.simulate_season(
            current_wins, current_losses, {}, [], current_ratings,
            self.TEAM_CONFERENCE, self._score_teams, simulations=10, rng=random.Random(1),
        )

        assert result[self.STRONG_TEAM]["bowl_probability"] == 0.0

    def test_same_seed_is_deterministic(self):
        current_wins = {self.STRONG_TEAM: 5, self.WEAK_TEAM: 5}
        current_losses = {self.STRONG_TEAM: 0, self.WEAK_TEAM: 0}
        remaining_games = [(self.STRONG_TEAM, self.WEAK_TEAM)]
        current_ratings = {self.STRONG_TEAM: 1600, self.WEAK_TEAM: 1500}

        first = season_simulation.simulate_season(
            current_wins, current_losses, {}, remaining_games, current_ratings,
            self.TEAM_CONFERENCE, self._score_teams, simulations=50, rng=random.Random(99),
        )
        second = season_simulation.simulate_season(
            current_wins, current_losses, {}, remaining_games, current_ratings,
            self.TEAM_CONFERENCE, self._score_teams, simulations=50, rng=random.Random(99),
        )

        assert first == second

    def test_a_game_against_an_untracked_team_is_skipped_not_a_crash(self):
        # "fcs" has no entry in TEAM_CONFERENCE -- see season_projection.
        # _season_standings_inputs' own docstring for why a remaining_games
        # pairing like this should never reach here in production, but
        # this is the defensive guard in case one does.
        current_wins = {self.STRONG_TEAM: 5}
        current_losses = {self.STRONG_TEAM: 0}
        remaining_games = [(self.STRONG_TEAM, "fcs")]
        current_ratings = {self.STRONG_TEAM: 1600}

        result = season_simulation.simulate_season(
            current_wins, current_losses, {}, remaining_games, current_ratings,
            self.TEAM_CONFERENCE, self._score_teams, simulations=10, rng=random.Random(1),
        )

        assert result[self.STRONG_TEAM]["projected_wins"] == pytest.approx(5.0)
