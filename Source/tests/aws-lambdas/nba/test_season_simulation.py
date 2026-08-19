"""
Unit tests for the Season tab's Monte Carlo simulation (regular season +
play-in + playoffs) and the NBA Cup (in-season tournament) simulation. No
AWS involved -- every function here is pure, same testability philosophy
as tests/library/features/test_nba.py. Private helpers (_seed_conference,
_simulate_play_in, _simulate_bracket) are tested directly with small
custom setups, matching how this repo already tests NFL's own equivalents
directly rather than only through simulate_season.
"""
import random

import pytest

import season_simulation


class TestSeedConference:
    def test_seeds_ordered_by_record_descending(self):
        wins = {"a": 10, "b": 8, "c": 3, "d": 9}

        seeds = season_simulation._seed_conference(["a", "b", "c", "d"], wins, {})

        assert seeds == ["a", "d", "b", "c"]

    def test_ties_broken_by_point_differential(self):
        wins = {"a": 10, "b": 10}
        point_differential = {"a": -5, "b": 20}

        seeds = season_simulation._seed_conference(["a", "b"], wins, point_differential)

        assert seeds == ["b", "a"]


class TestSimulatePlayIn:
    def test_returns_two_distinct_teams_from_the_four_seeds(self):
        ratings = {team: 1500 for team in ("s7", "s8", "s9", "s10")}
        rng = random.Random(42)

        final_7, final_8 = season_simulation._simulate_play_in("s7", "s8", "s9", "s10", ratings, 55, rng)

        assert final_7 != final_8
        assert {final_7, final_8} <= {"s7", "s8", "s9", "s10"}

    def test_seed_10_can_never_win_the_final_7_seed(self):
        # Seed 10 only ever plays in Game 2 (9v10) -- winning that game
        # sends them to Game 3 for the 8 seed, never Game 1's 7-seed slot.
        ratings = {"s7": 1000, "s8": 1000, "s9": 1000, "s10": 2200}  # s10 wildly favored
        rng = random.Random(1)

        results = [
            season_simulation._simulate_play_in("s7", "s8", "s9", "s10", ratings, 55, rng)
            for _ in range(200)
        ]

        assert all(final_7 != "s10" for final_7, _ in results)

    def test_a_much_stronger_seed_7_reaches_the_bracket_far_more_often(self):
        ratings = {"s7": 2200, "s8": 1500, "s9": 1500, "s10": 1500}
        rng = random.Random(3)

        seven_seed_count = sum(
            1 for _ in range(500)
            if season_simulation._simulate_play_in("s7", "s8", "s9", "s10", ratings, 55, rng)[0] == "s7"
        )

        assert seven_seed_count / 500 > 0.8


class TestSimulateBracket:
    def test_returns_one_of_the_eight_seeds(self):
        seeds = [f"s{i}" for i in range(1, 9)]
        ratings = {team: 1500 for team in seeds}

        champion = season_simulation._simulate_bracket(seeds, ratings, home_advantage=55, rng=random.Random(42))

        assert champion in seeds

    def test_a_much_stronger_top_seed_wins_far_more_often_than_a_coin_flip(self):
        seeds = [f"s{i}" for i in range(1, 9)]
        ratings = {"s1": 2200}  # everyone else defaults to DEFAULT_STARTING_RATING (1500)
        rng = random.Random(1)

        championships = sum(
            1 for _ in range(500)
            if season_simulation._simulate_bracket(seeds, ratings, home_advantage=55, rng=rng) == "s1"
        )

        # A generic 1-in-8 baseline is 12.5% -- a 700-point Elo edge should
        # push this dramatically higher.
        assert championships / 500 > 0.5


class TestSeriesWinProbability:
    def test_a_coinflip_series_from_scratch_is_a_coinflip(self):
        assert season_simulation.series_win_probability(0.5, 0, 0) == pytest.approx(0.5)

    def test_already_leading_four_is_a_certain_win_regardless_of_game_probability(self):
        assert season_simulation.series_win_probability(0.01, 4, 2) == 1.0

    def test_already_trailing_four_is_a_certain_loss_regardless_of_game_probability(self):
        assert season_simulation.series_win_probability(0.99, 2, 4) == 0.0

    def test_a_favorite_leading_the_series_has_higher_win_probability_than_starting_fresh(self):
        fresh = season_simulation.series_win_probability(0.6, 0, 0)
        leading = season_simulation.series_win_probability(0.6, 2, 1)
        assert leading > fresh

    def test_a_true_toss_up_series_is_more_certain_than_a_single_game(self):
        # Best-of-7 compounds a per-game edge -- even a small one (55%)
        # should be a much stronger series favorite than a single game.
        single_game = 0.55
        series = season_simulation.series_win_probability(single_game, 0, 0)
        assert series > single_game

    def test_symmetric_for_the_trailing_side(self):
        # P(A wins | A leads 3-2) should equal 1 - P(B wins | B trails 2-3)
        # -- i.e. plugging in B's own game probability and swapping the
        # win counts gives the complementary result.
        a_wins = season_simulation.series_win_probability(0.7, 3, 2)
        b_wins = season_simulation.series_win_probability(0.3, 2, 3)
        assert a_wins == pytest.approx(1 - b_wins)


class TestPredictedSeriesScore:
    def test_a_coinflip_series_from_scratch_predicts_the_longest_close_finish(self):
        # Counter-intuitive but correct: for a true coinflip (p=0.5), 4-2
        # and 4-3 are EACH more probable than 4-0 -- a longer series has
        # combinatorially more ways to reach that exact record (C(5,2)=10,
        # C(6,3)=20) than a sweep has (C(3,0)=1), and that multiplicity
        # advantage outweighs the shrinking p^4*(1-p)^k term through k=2,3
        # at p=0.5 specifically. 4-2/4-3 (and their mirror 2-4/3-4) are an
        # exact 4-way tie for the true maximum.
        assert season_simulation.predicted_series_score(0.5, 0, 0) in {(4, 2), (4, 3), (2, 4), (3, 4)}

    def test_a_big_favorite_is_predicted_to_sweep(self):
        assert season_simulation.predicted_series_score(0.95, 0, 0) == (4, 0)

    def test_already_leading_four_predicts_the_series_already_decided(self):
        assert season_simulation.predicted_series_score(0.5, 4, 2) == (4, 2)

    def test_a_live_in_progress_record_only_predicts_the_games_left_to_play(self):
        # Leading 3-1 with any positive game probability -- the predicted
        # final score's own trailing side can only be 1, 2, or 3 (the
        # already-played record is fixed, not re-predicted).
        predicted_a, predicted_b = season_simulation.predicted_series_score(0.6, 3, 1)
        assert predicted_a == 4
        assert predicted_b in {1, 2, 3}

    def test_symmetric_for_the_trailing_side(self):
        a_perspective = season_simulation.predicted_series_score(0.8, 0, 0)
        b_perspective = season_simulation.predicted_series_score(0.2, 0, 0)
        assert a_perspective == (b_perspective[1], b_perspective[0])


class TestProjectPlayIn:
    def test_seed_ten_can_never_win_the_final_seven_or_eight_seed(self):
        ratings = {team: 1500 for team in ("s7", "s8", "s9", "s10")}

        result = season_simulation.project_play_in("s7", "s8", "s9", "s10", ratings, home_advantage=55)

        assert result["final_7_seed"] != "s10"
        assert result["final_8_seed"] != "s10"

    def test_returns_three_games_and_no_rng_needed(self):
        ratings = {team: 1500 for team in ("s7", "s8", "s9", "s10")}

        first = season_simulation.project_play_in("s7", "s8", "s9", "s10", ratings, home_advantage=55)
        second = season_simulation.project_play_in("s7", "s8", "s9", "s10", ratings, home_advantage=55)

        assert len(first["games"]) == 3
        assert first == second

    def test_a_dominant_seven_seed_wins_the_final_seven_seed_outright(self):
        ratings = {"s7": 2200, "s8": 1500, "s9": 1500, "s10": 1500}

        result = season_simulation.project_play_in("s7", "s8", "s9", "s10", ratings, home_advantage=55)

        assert result["final_7_seed"] == "s7"

    def test_games_are_ordered_to_match_the_quarterfinal_they_feed(self):
        # The bracket UI lays Play-In games out top-to-bottom in list
        # order; Conference Quarterfinals' own (1v8) pair sits above
        # (2v7), so the game deciding the 8 seed must come first and the
        # 7v8 game (which decides the 7 seed) must come last.
        ratings = {team: 1500 for team in ("s7", "s8", "s9", "s10")}

        result = season_simulation.project_play_in("s7", "s8", "s9", "s10", ratings, home_advantage=55)

        assert result["games"][0]["predicted_winner"] == result["final_8_seed"]
        assert {result["games"][-1]["team_a"], result["games"][-1]["team_b"]} == {"s7", "s8"}
        assert result["games"][-1]["predicted_winner"] == result["final_7_seed"]


class TestProjectBracket:
    SEEDS = [f"s{i}" for i in range(1, 9)]

    def test_returns_three_rounds_with_the_right_matchup_counts(self):
        ratings = {team: 1500 for team in self.SEEDS}

        result = season_simulation.project_bracket(self.SEEDS, ratings, home_advantage=55)

        rounds = {r["round"]: r["matchups"] for r in result["rounds"]}
        assert len(rounds["Conference Quarterfinals"]) == 4
        assert len(rounds["Conference Semifinals"]) == 2
        assert len(rounds["Conference Finals"]) == 1
        assert result["champion"] in self.SEEDS

    def test_no_reseeding_first_round_pairs_are_fixed(self):
        ratings = {team: 1500 for team in self.SEEDS}

        result = season_simulation.project_bracket(self.SEEDS, ratings, home_advantage=55)

        first_round_pairs = {frozenset((m["team_a"], m["team_b"])) for m in result["rounds"][0]["matchups"]}
        assert first_round_pairs == {
            frozenset(("s1", "s8")), frozenset(("s4", "s5")), frozenset(("s3", "s6")), frozenset(("s2", "s7")),
        }

    def test_a_much_stronger_top_seed_is_favored_to_win_it_all(self):
        ratings = {"s1": 2200}

        result = season_simulation.project_bracket(self.SEEDS, ratings, home_advantage=55)

        assert result["champion"] == "s1"


class TestProjectConferenceBracket:
    def test_combines_play_in_and_bracket_into_five_rounds(self):
        direct_seeds = [f"s{i}" for i in range(1, 7)]
        ratings = {f"s{i}": 1500 for i in range(1, 11)}

        result = season_simulation.project_conference_bracket(
            direct_seeds, "s7", "s8", "s9", "s10", ratings, home_advantage=55,
        )

        assert [r["round"] for r in result["rounds"]] == [
            "Play-In", "Play-In Elimination", "Conference Quarterfinals", "Conference Semifinals", "Conference Finals",
        ]
        all_teams = {f"s{i}" for i in range(1, 11)}
        assert result["champion"] in all_teams

    def test_play_in_round_has_two_games_and_elimination_round_has_one(self):
        direct_seeds = [f"s{i}" for i in range(1, 7)]
        ratings = {f"s{i}": 1500 for i in range(1, 11)}

        result = season_simulation.project_conference_bracket(
            direct_seeds, "s7", "s8", "s9", "s10", ratings, home_advantage=55,
        )

        rounds = {r["round"]: r["matchups"] for r in result["rounds"]}
        assert len(rounds["Play-In"]) == 2
        assert len(rounds["Play-In Elimination"]) == 1
        # The elimination game's own participants are game 1's loser and
        # game 2's winner -- neither is a fresh seed_7-10 team id on its
        # own, both trace back to one of the first round's two games.
        game1, game2 = rounds["Play-In"]
        elimination = rounds["Play-In Elimination"][0]
        first_round_teams = {game1["team_a"], game1["team_b"], game2["team_a"], game2["team_b"]}
        assert elimination["team_a"] in first_round_teams
        assert elimination["team_b"] in first_round_teams


class TestProjectFinals:
    def test_home_court_goes_to_the_better_regular_season_record(self):
        ratings = {"a": 1500, "b": 1500}

        result = season_simulation.project_finals(
            "a", "b", wins={"a": 60, "b": 50}, point_differential={}, ratings=ratings, home_advantage=55,
        )

        assert result["team_a"] == "a"

    def test_point_differential_breaks_a_tied_record(self):
        ratings = {"a": 1500, "b": 1500}

        result = season_simulation.project_finals(
            "a", "b", wins={"a": 55, "b": 55}, point_differential={"a": -10, "b": 200},
            ratings=ratings, home_advantage=55,
        )

        assert result["team_a"] == "b"


class TestSimulateSeason:
    # Real ESPN team_ids from library.features.nba_teams.TEAM_DIVISIONS --
    # _teams_by_conference/_teams_by_division read the real table, so a
    # meaningful end-to-end test needs real ids to land in real
    # conferences/divisions. "13"=LAL, "12"=LAC -- both Western Pacific.
    STRONG_TEAM = "13"
    WEAK_TEAM = "12"

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
            for key in (
                "division_winner_probability", "play_in_probability",
                "playoff_probability", "championship_probability",
            ):
                assert 0.0 <= team_projection[key] <= 1.0

    def test_a_dominant_team_makes_the_playoffs_almost_every_path_without_needing_the_play_in(self):
        # An extreme Elo edge over its entire conference should push a
        # team to a top-6 seed (direct bracket berth) almost every path --
        # playoff_probability high, play_in_probability near zero.
        current_wins = {self.STRONG_TEAM: 20, self.WEAK_TEAM: 0}
        current_losses = {self.STRONG_TEAM: 0, self.WEAK_TEAM: 20}
        current_ratings = {self.STRONG_TEAM: 2400, self.WEAK_TEAM: 1000}

        result = season_simulation.simulate_season(
            current_wins, current_losses, {}, [], current_ratings,
            simulations=100, rng=random.Random(11),
        )

        assert result[self.STRONG_TEAM]["playoff_probability"] > 0.9
        assert result[self.STRONG_TEAM]["play_in_probability"] < 0.1

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


class TestSimulateCup:
    def test_none_when_season_is_not_in_cup_groups(self):
        result = season_simulation.simulate_cup(2099, {}, {}, [], {}, simulations=10, rng=random.Random(1))
        assert result is None

    def test_none_when_season_is_none(self):
        result = season_simulation.simulate_cup(None, {}, {}, [], {}, simulations=10, rng=random.Random(1))
        assert result is None

    def test_returns_every_team_in_the_seasons_groups(self):
        result = season_simulation.simulate_cup(2026, {}, {}, [], {}, simulations=10, rng=random.Random(1))

        assert result is not None
        assert len(result) == 30

    def test_probabilities_are_fractions_between_zero_and_one(self):
        result = season_simulation.simulate_cup(2026, {}, {}, [], {}, simulations=50, rng=random.Random(2))

        for team_projection in result.values():
            for key in ("group_winner_probability", "knockout_probability", "cup_finalist_probability", "champion_probability"):
                assert 0.0 <= team_projection[key] <= 1.0

    def test_group_field_matches_nba_cup_groups(self):
        result = season_simulation.simulate_cup(2026, {}, {}, [], {}, simulations=10, rng=random.Random(1))

        assert result["2"]["group"] == "Eastern B"  # BOS

    def test_real_group_wins_losses_are_carried_through_untouched(self):
        result = season_simulation.simulate_cup(
            2026, cup_wins={"2": 4}, cup_losses={"2": 1}, remaining_cup_games=[], current_ratings={},
            simulations=10, rng=random.Random(1),
        )

        assert result["2"]["group_wins"] == 4
        assert result["2"]["group_losses"] == 1

    def test_a_dominant_team_wins_its_group_far_more_often_than_a_coin_flip(self):
        # BOS ("2") is in Eastern B alongside ORL/DET/PHI/BKN -- a huge
        # rating edge over the whole group should push group_winner_
        # probability well above a generic 1-in-5 baseline (20%).
        ratings = {"2": 2400}  # everyone else defaults to DEFAULT_STARTING_RATING
        remaining = [("2", "19"), ("8", "2"), ("2", "20"), ("17", "2")]  # BOS vs each groupmate once

        result = season_simulation.simulate_cup(
            2026, cup_wins={}, cup_losses={}, remaining_cup_games=remaining, current_ratings=ratings,
            simulations=300, rng=random.Random(5),
        )

        assert result["2"]["group_winner_probability"] > 0.5

    def test_an_unrecognized_team_id_in_remaining_games_is_skipped_defensively(self):
        remaining = [("2", "999")]  # "999" isn't a real franchise id

        result = season_simulation.simulate_cup(
            2026, cup_wins={}, cup_losses={}, remaining_cup_games=remaining, current_ratings={},
            simulations=10, rng=random.Random(1),
        )

        assert result is not None  # doesn't raise -- the bad pair is simply skipped


class TestProjectCupKnockoutBracket:
    def test_none_when_season_is_not_in_cup_groups(self):
        result = season_simulation.project_cup_knockout_bracket(2099, {}, {}, {})
        assert result is None

    def test_none_when_season_is_none(self):
        result = season_simulation.project_cup_knockout_bracket(None, {}, {}, {})
        assert result is None

    def test_returns_both_conferences_a_championship_and_a_champion(self):
        result = season_simulation.project_cup_knockout_bracket(2026, {}, {}, {})

        assert set(result["conferences"]) == {"Eastern", "Western"}
        for rounds in result["conferences"].values():
            assert [r["round"] for r in rounds] == ["Semifinals", "Conference Final"]
            assert len(rounds[0]["matchups"]) == 2
        assert result["champion"] == result["championship"]["predicted_winner"]

    def test_every_matchup_has_a_status_field(self):
        # Regression for a real production crash (2026-08-19): every
        # matchup here comes straight from project_matchup, which has no
        # "status" key at all -- the frontend's BracketMatchup.fromJson
        # requires one on every matchup regardless of bracket type.
        result = season_simulation.project_cup_knockout_bracket(2026, {}, {}, {})

        for rounds in result["conferences"].values():
            for round_ in rounds:
                for matchup in round_["matchups"]:
                    assert matchup["status"] == "projected"
        assert result["championship"]["status"] == "projected"

    def test_no_rng_needed_and_deterministic_across_calls(self):
        cup_wins = {"2": 4, "19": 3, "8": 2, "20": 1, "17": 0}

        first = season_simulation.project_cup_knockout_bracket(2026, cup_wins, {}, {})
        second = season_simulation.project_cup_knockout_bracket(2026, cup_wins, {}, {})

        assert first == second

    def test_a_dominant_group_winner_is_favored_to_win_its_conference(self):
        # Boston ("2", real CUP_GROUPS Eastern-B membership) given a real
        # group record so it's unambiguously its group's winner, then
        # rated far above the rest of its own conference's knockout field.
        cup_wins = {"2": 5}
        ratings = {"2": 2400}

        result = season_simulation.project_cup_knockout_bracket(2026, cup_wins, {}, ratings)

        eastern_final = result["conferences"]["Eastern"][1]["matchups"][0]
        assert eastern_final["predicted_winner"] == "2"


class TestProjectLeaderboard:
    def test_projects_current_plus_per_game_times_remaining(self):
        result = season_simulation.project_leaderboard(
            current_totals={"p1": 800.0},
            per_game_projections={"p1": 25.0},
            games_remaining={"p1": 4},
            top_n=10,
        )

        assert result[0]["projected_total"] == pytest.approx(800.0 + 25.0 * 4)

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
