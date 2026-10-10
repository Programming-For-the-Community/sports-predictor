"""
Unit tests for the NHL season simulation: playoff-field selection, the
points-then-regulation-wins tiebreak, series maths, the Monte Carlo
totals and the projected bracket. Pure functions, no mocks.

conftest.py puts aws-lambdas/nhl/predict on sys.path.
"""
import pytest
import season_simulation as sim

from library.features import nhl_teams

ATLANTIC = ["1", "2", "5", "26", "10", "14", "20", "21"]
METROPOLITAN = ["7", "29", "11", "12", "13", "15", "16", "23"]


def _points_by_order(*divisions, start=120, step=2):
    """Points falling by `step` down each division's listed order."""
    return {team: start - step * index for division in divisions for index, team in enumerate(division)}


def _all_points(**overrides):
    points = {team: 60 for team in nhl_teams.TEAM_DIVISIONS}
    points.update(overrides)
    return points


class TestAlignment:
    def test_thirty_two_current_teams_in_four_divisions_of_eight(self):
        divisions = sim.teams_by_division()

        assert sorted(len(teams) for teams in divisions.values()) == [8, 8, 8, 8]
        assert nhl_teams.ARIZONA_TEAM_ID not in divisions["Western Central"]
        assert nhl_teams.UTAH_TEAM_ID in divisions["Western Central"]
        assert {sim.conference_of(division) for division in divisions} == {"Eastern", "Western"}


class TestConferenceFields:
    def test_top_three_per_division_plus_the_two_best_of_the_rest(self):
        # Atlantic runs 120..106, Metropolitan 110..96: the Atlantic's
        # fourth and fifth (114, 112) are the wildcards.
        points = _all_points(**_points_by_order(ATLANTIC), **_points_by_order(METROPOLITAN, start=110))

        east = sim.conference_fields(points, {}, {})["Eastern"]

        assert east["teams"] == {"1", "2", "5", "26", "10", "7", "29", "11"}
        assert set(east["division_winners"]) == {"1", "7"}
        # The better division winner draws the second wildcard.
        assert east["brackets"] == [["1", "10", "2", "5"], ["7", "26", "29", "11"]]
        assert east["seeds"]["1"] == 1
        assert east["seeds"]["7"] == 6

    def test_level_on_points_breaks_on_regulation_wins_then_wins(self):
        points = _all_points(**{"1": 100, "2": 100, "5": 100})

        east = sim.conference_fields(points, {"1": 30, "2": 35, "5": 30}, {"1": 44, "2": 40, "5": 46})

        assert east["Eastern"]["brackets"][0][0] == "2"
        assert east["Eastern"]["brackets"][0][2:] == ["5", "1"]


class TestSeries:
    def test_an_even_series_is_a_coin_flip_and_a_decided_one_is_certain(self):
        assert sim.series_win_probability(0.5, 0.5) == pytest.approx(0.5)
        assert sim.series_win_probability(0.6, 0.6, wins_a=4, wins_b=2) == 1.0
        assert sim.series_win_probability(0.6, 0.6, wins_a=1, wins_b=4) == 0.0

    def test_home_ice_favours_the_higher_seed_between_equal_teams(self):
        assert sim.series_win_probability(0.55, 0.45) > 0.5

    def test_matches_the_best_of_seven_closed_form(self):
        p = 0.6
        closed_form = sum(p ** 4 * (1 - p) ** k * c for k, c in enumerate((1, 4, 10, 20)))

        assert sim.series_win_probability(p, p) == pytest.approx(closed_form)

    def test_predicted_score_names_four_wins_for_the_favourite(self):
        assert sim.predicted_series_score(0.9, 0.9) == (4, 0)
        wins_a, wins_b = sim.predicted_series_score(0.3, 0.3)
        assert wins_b == 4
        assert wins_a < 4

    def test_game_probability_rates_a_relocated_team_by_its_franchise(self):
        ratings = {nhl_teams.UTAH_TEAM_ID: 1600.0, "1": 1500.0}

        assert sim.game_probability(nhl_teams.ARIZONA_TEAM_ID, "1", ratings) == sim.game_probability(nhl_teams.UTAH_TEAM_ID, "1", ratings)
        assert sim.game_probability("1", "2", {}, home_advantage=0.0) == pytest.approx(0.5)


class TestSimulateSeason:
    def _simulate(self, **kwargs):
        defaults = {"wins": {}, "losses": {}, "overtime_losses": {}, "regulation_wins": {}, "remaining_games": [], "ratings": {}}
        return sim.simulate_season(**{**defaults, **kwargs}, simulations=200, seed=7)

    def test_every_remaining_game_awards_one_win_and_one_loss(self):
        result = self._simulate(remaining_games=[("1", "2")] * 10)

        assert result["1"]["projected_wins"] + result["2"]["projected_wins"] == pytest.approx(10)
        assert result["1"]["projected_wins"] + result["1"]["projected_losses"] == pytest.approx(10)
        # Overtime losses are counted inside projected_losses and worth a point.
        assert 0 < result["1"]["projected_overtime_losses"] < result["1"]["projected_losses"]
        assert result["1"]["projected_points"] == pytest.approx(
            2 * result["1"]["projected_wins"] + result["1"]["projected_overtime_losses"],
        )

    def test_probabilities_sum_to_the_number_of_places(self):
        result = self._simulate()

        assert sum(row["playoff_probability"] for row in result.values()) == pytest.approx(16)
        assert sum(row["division_winner_probability"] for row in result.values()) == pytest.approx(4)
        assert sum(row["championship_probability"] for row in result.values()) == pytest.approx(1)
        assert len(result) == 32

    def test_a_finished_season_fixes_the_field(self):
        wins = {team: points // 2 for team, points in _all_points(
            **_points_by_order(ATLANTIC), **_points_by_order(METROPOLITAN, start=118),
        ).items()}

        result = self._simulate(wins=wins, regulation_wins=wins)

        assert result["1"]["division_winner_probability"] == 1.0
        assert result["1"]["playoff_probability"] == 1.0
        assert result["21"]["playoff_probability"] == 0.0
        assert result["1"]["projected_wins"] == wins["1"]

    def test_a_much_stronger_team_wins_the_cup_more_often(self):
        result = self._simulate(ratings={"1": 1800.0}, remaining_games=[("1", "2")] * 20)

        assert result["1"]["championship_probability"] > 0.3
        assert result["1"]["playoff_probability"] == 1.0


class TestProjectBracket:
    def test_three_rounds_per_conference_then_the_final(self):
        points = _all_points(**_points_by_order(ATLANTIC), **_points_by_order(METROPOLITAN, start=110))

        bracket = sim.project_bracket(points, {}, {}, {"1": 1700.0})

        east = bracket["conferences"]["Eastern"]
        assert [(r["round"], len(r["matchups"])) for r in east] == [
            ("First Round", 4), ("Second Round", 2), ("Conference Final", 1),
        ]
        first = east[0]["matchups"][0]
        assert (first["team_a"], first["team_b"], first["seed_a"], first["status"]) == ("1", "10", 1, "projected")
        assert first["predicted_winner"] == "1"
        assert (first["predicted_wins_a"], first["win_probability"] > 0.5) == (4, True)
        assert east[2]["matchups"][0]["predicted_winner"] == "1"
        assert bracket["finals"]["round"] == "Stanley Cup Final"
        assert bracket["champion"] == "1"
        assert set(bracket["conferences"]) == {"Eastern", "Western"}

    def test_the_higher_seed_is_always_team_a(self):
        points = _all_points(**_points_by_order(ATLANTIC), **_points_by_order(METROPOLITAN, start=110))

        bracket = sim.project_bracket(points, {}, {}, {"10": 1900.0})

        # The wildcard upsets the division winner, then meets the 2-3 winner as the lower seed.
        second = bracket["conferences"]["Eastern"][1]["matchups"][0]
        assert (second["team_a"], second["team_b"], second["predicted_winner"]) == ("2", "10", "10")
        assert second["win_probability"] > 0.5


class TestProjectLeaderboard:
    def test_ranks_by_current_total_and_adds_the_per_game_estimate(self):
        board = sim.project_leaderboard({"a": 10, "b": 12, "c": 1}, {"a": 0.5, "b": 0.25}, {"a": 40, "b": 40}, top_n=2)

        assert board == [
            {"entity_id": "b", "current_total": 12, "projected_total": 22.0},
            {"entity_id": "a", "current_total": 10, "projected_total": 30.0},
        ]


class TestProjectMatchup:
    def test_a_side_with_no_opponent_yet_advances(self):
        matchup = sim.project_matchup("1", None, {"1": 1}, {}, 25.0)

        assert (matchup["predicted_winner"], matchup["win_probability"], matchup["seed_b"]) == ("1", 1.0, None)

    def test_nothing_is_predicted_with_neither_side_known(self):
        matchup = sim.project_matchup(None, None, {}, {}, 25.0)

        assert (matchup["predicted_winner"], matchup["win_probability"]) == (None, None)
