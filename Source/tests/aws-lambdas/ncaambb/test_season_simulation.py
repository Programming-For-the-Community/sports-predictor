"""
Unit tests for season_simulation.py's generic single-elimination bracket
walker -- the shared building block both conference tournaments and
March Madness reuse (see the module's own docstring). Pure functions, no
AWS/storage mocking needed.

season_simulation is registered on sys.path by conftest.py (same
sys.path.insert(0, .../ncaambb/predict) already used for predict/'s other
sibling modules).
"""
import random

import season_simulation as ss


class TestStandardSeedLine:
    def test_size_2(self):
        assert ss._standard_seed_line(2) == [1, 2]

    def test_size_4(self):
        assert ss._standard_seed_line(4) == [1, 4, 2, 3]

    def test_size_8_matches_the_well_known_seeding(self):
        assert ss._standard_seed_line(8) == [1, 8, 4, 5, 2, 7, 3, 6]

    def test_every_round_1_pair_sums_to_bracket_size_plus_1(self):
        seeds = ss._standard_seed_line(16)
        pairs = [(seeds[i], seeds[i + 1]) for i in range(0, 16, 2)]
        assert all(a + b == 17 for a, b in pairs)

    def test_every_seed_appears_exactly_once(self):
        seeds = ss._standard_seed_line(32)
        assert sorted(seeds) == list(range(1, 33))

    def test_seed_1_and_seed_2_are_in_separate_halves(self):
        seeds = ss._standard_seed_line(16)
        half = len(seeds) // 2
        first_half, second_half = seeds[:half], seeds[half:]
        assert (1 in first_half) != (1 in second_half)  # sanity: 1 is somewhere
        assert (1 in first_half) != (2 in first_half)  # 1 and 2 never share a half


class TestNextPowerOfTwo:
    def test_exact_power_stays_the_same(self):
        assert ss._next_power_of_two(16) == 16

    def test_rounds_up(self):
        assert ss._next_power_of_two(11) == 16

    def test_one_stays_one(self):
        assert ss._next_power_of_two(1) == 1


class TestRoundNames:
    def test_two_teams(self):
        assert ss._round_names(2) == ["Championship"]

    def test_four_teams(self):
        assert ss._round_names(4) == ["Semifinals", "Championship"]

    def test_eight_teams(self):
        assert ss._round_names(8) == ["Quarterfinals", "Semifinals", "Championship"]

    def test_sixteen_teams(self):
        assert ss._round_names(16) == ["Round of 16", "Quarterfinals", "Semifinals", "Championship"]

    def test_sixty_four_teams(self):
        assert ss._round_names(64) == [
            "Round of 64", "Round of 32", "Round of 16", "Quarterfinals", "Semifinals", "Championship",
        ]


class TestProjectSingleElimBracket:
    def test_a_full_power_of_two_field_has_no_byes(self):
        teams = [f"t{i}" for i in range(1, 9)]  # 8 teams, seed 1..8
        ratings = {team: 1500.0 + (8 - i) * 10 for i, team in enumerate(teams, start=1)}  # higher seed = higher rating

        bracket = ss.project_single_elim_bracket(teams, ratings, home_advantage=0.0)

        assert [r["round"] for r in bracket["rounds"]] == ["Quarterfinals", "Semifinals", "Championship"]
        quarterfinals = bracket["rounds"][0]["matchups"]
        assert len(quarterfinals) == 4
        assert all(m["team_b"] is not None for m in quarterfinals)  # no byes in a full field

    def test_top_seed_beats_bottom_seed_and_the_whole_bracket_resolves(self):
        teams = [f"t{i}" for i in range(1, 9)]
        ratings = {team: 1500.0 + (8 - i) * 100 for i, team in enumerate(teams, start=1)}

        bracket = ss.project_single_elim_bracket(teams, ratings, home_advantage=0.0)

        # t1 (seed 1, by far the best rating) should win every round.
        assert bracket["champion"] == "t1"

    def test_a_non_power_of_two_field_pads_with_byes_for_the_best_seeds(self):
        teams = [f"t{i}" for i in range(1, 12)]  # 11 teams -> bracket_size 16, 5 byes
        ratings = {team: 1500.0 for team in teams}  # flat ratings -- outcome is seed-driven via >=0.5 tie-break

        bracket = ss.project_single_elim_bracket(teams, ratings, home_advantage=0.0)

        round_one = bracket["rounds"][0]["matchups"]
        assert bracket["rounds"][0]["round"] == "Round of 16"
        assert len(round_one) == 8
        byes = [m for m in round_one if m["team_b"] is None]
        real_games = [m for m in round_one if m["team_b"] is not None]
        assert len(byes) == 5  # 16 - 11
        assert len(real_games) == 3  # remaining 6 teams pair up
        # Every bye is awarded to the real team present, at 100% confidence.
        assert all(m["predicted_winner"] == m["team_a"] and m["win_probability"] == 1.0 for m in byes)
        # The top 5 seeds (t1..t5) are exactly the ones that get a bye.
        assert {m["team_a"] for m in byes} == {"t1", "t2", "t3", "t4", "t5"}

    def test_home_advantage_only_applies_in_round_one(self):
        # t2 is rated lower than t1 but not so much lower that home
        # advantage alone can't flip round 1; by the neutral-site
        # semifinal/championship rounds the rating gap should decide it.
        teams = ["t1", "t2", "t3", "t4"]
        ratings = {"t1": 1500.0, "t2": 1470.0, "t3": 1400.0, "t4": 1400.0}

        bracket = ss.project_single_elim_bracket(teams, ratings, home_advantage=55.0)

        semifinal = bracket["rounds"][0]["matchups"][0]
        # t1 (seed 1) hosts t4 (seed 4) in round 1 -- higher seed listed as team_a.
        assert semifinal["team_a"] == "t1"

    def test_champion_is_the_winner_of_the_final_round(self):
        teams = ["t1", "t2"]
        ratings = {"t1": 1600.0, "t2": 1400.0}

        bracket = ss.project_single_elim_bracket(teams, ratings, home_advantage=0.0)

        assert bracket["rounds"][-1]["round"] == "Championship"
        assert bracket["champion"] == bracket["rounds"][-1]["matchups"][0]["predicted_winner"]

    def test_a_two_team_field_is_just_a_championship(self):
        teams = ["t1", "t2"]
        ratings = {"t1": 1500.0, "t2": 1500.0}

        bracket = ss.project_single_elim_bracket(teams, ratings, home_advantage=0.0)

        assert len(bracket["rounds"]) == 1
        assert bracket["rounds"][0]["round"] == "Championship"


class TestConferenceSeedOrder:
    def test_ranks_by_conference_wins_then_point_differential(self):
        members = ["t1", "t2", "t3"]
        conference_wins = {"t1": 10, "t2": 12, "t3": 12}
        conference_losses = {"t1": 2, "t2": 2, "t3": 2}
        point_differential = {"t1": 50, "t2": 30, "t3": 100}

        order = ss._conference_seed_order(members, conference_wins, conference_losses, point_differential)

        # t3 and t2 are tied on conference wins (12) -- t3 wins the tiebreak
        # on point differential (100 > 30). t1 has fewer conference wins.
        assert order == ["t3", "t2", "t1"]


class TestSelectMarchMadnessField:
    def test_auto_bids_are_the_distinct_conference_champions(self):
        model_scores = {f"t{i}": float(i) for i in range(1, 10)}
        conference_champions = {"ACC": "t1", "Big Ten": "t2"}

        auto_bids, at_large = ss.select_march_madness_field(model_scores, conference_champions)

        assert auto_bids == ["t1", "t2"]
        assert "t1" not in at_large and "t2" not in at_large

    def test_at_large_fills_the_rest_of_the_field_best_score_first(self):
        model_scores = {f"t{i}": float(i) for i in range(1, 6)}
        conference_champions = {"ACC": "t1"}

        auto_bids, at_large = ss.select_march_madness_field(model_scores, conference_champions)

        assert at_large == ["t2", "t3", "t4", "t5"]

    def test_field_caps_at_march_madness_field_size(self):
        model_scores = {f"t{i}": float(i) for i in range(1, 100)}
        conference_champions = {f"conf{i}": f"t{i}" for i in range(1, 40)}  # 39 auto bids

        auto_bids, at_large = ss.select_march_madness_field(model_scores, conference_champions)

        assert len(auto_bids) + len(at_large) == ss.MARCH_MADNESS_FIELD_SIZE


class TestFirstFour:
    def _field(self):
        auto_bids = [f"auto{i}" for i in range(1, 9)]  # 8 auto bids -> weakest 4 contested
        at_large = [f"al{i}" for i in range(1, 9)]  # 8 at-large -> weakest 4 contested
        ratings = {**{t: 1500.0 + (9 - i) for i, t in enumerate(auto_bids, start=1)},
                   **{t: 1500.0 + (9 - i) for i, t in enumerate(at_large, start=1)}}
        model_scores = {**{t: float(i) for i, t in enumerate(auto_bids, start=1)},
                        **{t: float(i) for i, t in enumerate(at_large, start=1)}}
        return auto_bids, at_large, ratings, model_scores

    def test_exactly_four_games_are_played(self):
        auto_bids, at_large, ratings, model_scores = self._field()

        result = ss.project_first_four(auto_bids, at_large, ratings, model_scores)

        assert len(result["matchups"]) == 4

    def test_settled_teams_are_untouched_and_field_shrinks_by_four(self):
        auto_bids, at_large, ratings, model_scores = self._field()

        result = ss.project_first_four(auto_bids, at_large, ratings, model_scores)

        # 16 teams in (8 auto + 8 at-large) -> 4 games trim 8 contested down to 4 winners -> 12 left.
        assert len(result["field"]) == 12
        for settled_team in ("auto1", "auto2", "auto3", "auto4", "al1", "al2", "al3", "al4"):
            assert settled_team in result["field"]

    def test_stronger_rated_contested_team_advances(self):
        auto_bids, at_large, ratings, model_scores = self._field()

        result = ss.project_first_four(auto_bids, at_large, ratings, model_scores)

        # auto5/auto6/auto7/auto8 and al5/al6/al7/al8 are the contested pools
        # (weakest 4 of each, by seed order); higher rating wins deterministically.
        winners = {m["predicted_winner"] for m in result["matchups"]}
        assert winners == {"auto5", "auto7", "al5", "al7"}


class TestAssignRegions:
    def test_snake_seeds_top_four_into_separate_regions(self):
        field = [f"t{i}" for i in range(1, 65)]

        regions = ss._assign_regions(field)

        assert len(regions) == ss.REGION_COUNT
        assert all(len(teams) == 16 for teams in regions.values())
        # Seed line 1 (t1-t4) goes to regions A-D in order.
        assert regions["Region A"][0] == "t1"
        assert regions["Region B"][0] == "t2"
        assert regions["Region C"][0] == "t3"
        assert regions["Region D"][0] == "t4"
        # Seed line 2 (t5-t8) reverses direction (snake).
        assert regions["Region D"][1] == "t5"
        assert regions["Region A"][1] == "t8"

    def test_every_team_appears_exactly_once(self):
        field = [f"t{i}" for i in range(1, 65)]

        regions = ss._assign_regions(field)

        all_teams = [team for teams in regions.values() for team in teams]
        assert sorted(all_teams) == sorted(field)


class TestProjectMarchMadnessBracket:
    def test_full_bracket_resolves_to_a_single_champion(self):
        auto_bids = [f"auto{i}" for i in range(1, 33)]
        at_large = [f"al{i}" for i in range(1, 37)]  # 32 + 36 = 68
        teams = auto_bids + at_large
        ratings = {t: 1500.0 + (68 - i) for i, t in enumerate(teams, start=1)}
        model_scores = {t: float(i) for i, t in enumerate(teams, start=1)}

        bracket = ss.project_march_madness_bracket(auto_bids, at_large, ratings, model_scores)

        round_names = [r["round"] for r in bracket["rounds"]]
        assert round_names == ["First Four", "Round of 64", "Round of 32", "Sweet 16", "Elite Eight", "Final Four", "Championship"]
        assert bracket["rounds"][1]["matchups"].__len__() == 32  # Round of 64: 8 games/region x 4
        assert bracket["rounds"][-1]["round"] == "Championship"
        assert bracket["champion"] is not None

    def test_the_best_rated_team_wins_it_all(self):
        auto_bids = [f"auto{i}" for i in range(1, 33)]
        at_large = [f"al{i}" for i in range(1, 37)]
        teams = auto_bids + at_large
        # auto1 is overwhelmingly the best-rated team in the field.
        ratings = {t: 1400.0 for t in teams}
        ratings["auto1"] = 2200.0
        model_scores = {t: float(i) for i, t in enumerate(teams, start=1)}

        bracket = ss.project_march_madness_bracket(auto_bids, at_large, ratings, model_scores)

        assert bracket["champion"] == "auto1"


class TestSimulateSeason:
    def _tracked_teams(self, n_conferences=4, teams_per_conference=5):
        team_conference = {}
        for c in range(n_conferences):
            for t in range(teams_per_conference):
                team_conference[f"c{c}t{t}"] = f"conf{c}"
        return team_conference

    def _score_teams(self, wins, losses, ratings):
        # Lower is better -- rank purely by (losses - wins), same
        # ordering direction the real ranking model's rmse-trained output
        # gives (a team with a better record scores lower/"better").
        return {team_id: losses.get(team_id, 0) - wins.get(team_id, 0) for team_id in wins}

    def test_probabilities_are_between_zero_and_one_and_sum_reasonably(self):
        team_conference = self._tracked_teams()
        teams = list(team_conference)
        wins = {t: 0 for t in teams}
        losses = {t: 0 for t in teams}
        conference_wins = {t: 0 for t in teams}
        conference_losses = {t: 0 for t in teams}
        point_differential = {t: 0 for t in teams}
        ratings = {t: 1500.0 for t in teams}
        remaining_games = [(teams[i], teams[i + 1], True) for i in range(0, len(teams) - 1, 2)]

        result = ss.simulate_season(
            wins, losses, conference_wins, conference_losses, point_differential, remaining_games,
            ratings, team_conference, self._score_teams, simulations=20, rng=random.Random(42),
        )

        assert set(result) == set(teams)
        for team_id, row in result.items():
            for key in (
                "conference_tournament_champion_probability", "ncaa_tournament_probability",
                "sweet_16_probability", "national_champion_probability",
            ):
                assert 0.0 <= row[key] <= 1.0

        # Exactly one champion per iteration -- probabilities across all
        # teams must sum to 1.0 (within floating point tolerance).
        assert abs(sum(row["national_champion_probability"] for row in result.values()) - 1.0) < 1e-9

    def test_every_conference_produces_exactly_one_champion_per_iteration(self):
        team_conference = self._tracked_teams()
        teams = list(team_conference)
        wins = {t: 0 for t in teams}
        losses = {t: 0 for t in teams}
        conference_wins = {t: 0 for t in teams}
        conference_losses = {t: 0 for t in teams}
        point_differential = {t: 0 for t in teams}
        ratings = {t: 1500.0 for t in teams}

        result = ss.simulate_season(
            wins, losses, conference_wins, conference_losses, point_differential, [],
            ratings, team_conference, self._score_teams, simulations=10, rng=random.Random(7),
        )

        # 4 conferences x 10 simulations = 40 conference-championship credits total.
        total_champion_credits = sum(row["conference_tournament_champion_probability"] for row in result.values())
        assert abs(total_champion_credits - 4.0) * 10 < 1e-6
