"""Unit tests for library.features.football_box, basketball_box and ncaafb_advanced."""
import math

from library.features import basketball_box, ncaafb_advanced
from library.features.football_box import football_box_columns
from library.storage.cfbd_supplements import CfbdSupplements

AS_OF = "2025-11-20"


def _row(stat_line, opponent_line, date="2025-11-10", points=(24, 17), periods=None, advanced=None):
    return {
        "event_key": date, "event_date": date, "stat_line": stat_line, "opponent_stat_line": opponent_line,
        "points_for": points[0], "points_against": points[1], "periods": periods, "advanced": advanced,
    }


NFL_OWN = {
    "total_yards": 350, "total_offensive_plays": 70, "net_passing_yards": 230, "pass_attempts": 32,
    "rushing_yards": 120, "rushing_attempts": 30, "sacks_taken": 2, "first_downs": 21, "turnovers": 1,
    "third_down_conversions": 6, "third_down_attempts": 14, "total_drives": 12, "red_zone_attempts": 4,
    "red_zone_conversions": 3, "fourth_down_attempts": 1, "fourth_down_conversions": 1,
}
NFL_OPP = {
    "total_yards": 280, "total_offensive_plays": 56, "net_passing_yards": 200, "pass_attempts": 30,
    "rushing_yards": 80, "rushing_attempts": 22, "sacks_taken": 4, "first_downs": 16, "turnovers": 3,
    "third_down_conversions": 3, "third_down_attempts": 12, "total_drives": 11, "red_zone_attempts": 2,
    "red_zone_conversions": 1,
}


class TestFootballBox:
    def test_nfl_offense_defense_and_margins(self):
        columns = football_box_columns("nfl", [_row(NFL_OWN, NFL_OPP)], [], 5, AS_OF)
        assert columns["home_yards_per_play"] == 5.0
        assert columns["home_yards_per_play_allowed"] == 5.0
        assert columns["home_net_yards_per_play_ewm"] == 0.0
        assert columns["home_turnover_margin"] == 2.0
        assert columns["home_sack_rate"] == 2 / 34
        assert columns["home_pressure_rate"] == 4 / 34
        assert columns["home_points_per_drive"] == 2.0
        assert math.isclose(columns["home_points_per_drive_allowed"], 17 / 11)
        assert columns["home_third_down_pct_allowed"] == 0.25
        assert columns["home_red_zone_pct_allowed"] == 0.5
        assert columns["home_third_down_pct_last10"] == 6 / 14
        assert columns["away_yards_per_play"] is None
        assert columns["diff_yards_per_play"] is None

    def test_existing_base_columns_are_not_repeated(self):
        columns = football_box_columns("nfl", [_row(NFL_OWN, NFL_OPP)], [], 5, AS_OF)
        assert "home_third_down_pct" not in columns and "home_avg_turnovers" not in columns
        assert "home_avg_turnovers_season_to_date" in columns

    def test_ncaafb_derives_plays_and_sacks_taken_from_the_opponents_sacks(self):
        own = {"total_yards": 400, "pass_attempts": 30, "rushing_attempts": 38, "turnovers": 0}
        opponent = {"sacks": 2, "turnovers": 1}
        columns = football_box_columns("ncaafb", [_row(own, opponent)], [], 5, AS_OF)
        assert columns["home_yards_per_play"] == 400 / 70
        assert columns["home_sack_rate"] == 2 / 32
        assert "home_points_per_drive" not in columns and "home_red_zone_trips" not in columns


BASKETBALL_OWN = {
    "field_goals_made": 40, "field_goal_attempts": 85, "three_pointers_made": 12, "three_point_attempts": 35,
    "free_throws_made": 18, "free_throw_attempts": 22, "offensive_rebounds": 10, "defensive_rebounds": 34,
    "turnovers": 12, "total_turnovers": 13, "points_in_paint": 44,
}
BASKETBALL_OPP = {
    "field_goals_made": 38, "field_goal_attempts": 88, "three_pointers_made": 10, "three_point_attempts": 30,
    "free_throws_made": 14, "free_throw_attempts": 18, "offensive_rebounds": 12, "defensive_rebounds": 30,
    "turnovers": 14, "total_turnovers": 14, "points_in_paint": 40,
}


class TestBasketballBox:
    def _possessions(self):
        own = 85 - 10 + 13 + 0.44 * 22
        opp = 88 - 12 + 14 + 0.44 * 18
        return (own + opp) / 2

    def test_ratings_and_four_factors(self):
        row = _row(BASKETBALL_OWN, BASKETBALL_OPP, points=(110, 100))
        columns = basketball_box.side_columns("nba", [row], 5, AS_OF)
        poss = self._possessions()
        assert math.isclose(columns["pace"], poss)
        assert math.isclose(columns["off_rating"], 110 / poss * 100)
        assert math.isclose(columns["net_rating"], 10 / poss * 100)
        assert columns["efg_pct"] == (40 + 6) / 85
        assert columns["efg_pct_allowed"] == (38 + 5) / 88
        assert columns["orb_pct"] == 10 / 40
        assert columns["drb_pct"] == 34 / 46
        assert math.isclose(columns["tov_pct"], 13 / (85 + 0.44 * 22 + 13))
        assert math.isclose(columns["paint_pts_per_100"], 44 / poss * 100)

    def test_overtime_scales_pace_to_regulation(self):
        regulation = basketball_box.side_columns("nba", [_row(BASKETBALL_OWN, BASKETBALL_OPP, periods=4)], 5, AS_OF)
        overtime = basketball_box.side_columns("nba", [_row(BASKETBALL_OWN, BASKETBALL_OPP, periods=5)], 5, AS_OF)
        assert math.isclose(overtime["pace"], regulation["pace"] * 48 / 53)
        college = basketball_box.side_columns("ncaambb", [_row(BASKETBALL_OWN, BASKETBALL_OPP, periods=3)], 5, AS_OF)
        assert math.isclose(college["pace"], self._possessions() * 40 / 45)

    def test_columns_have_home_away_and_diff(self):
        row = _row(BASKETBALL_OWN, BASKETBALL_OPP)
        columns = basketball_box.basketball_box_columns("ncaambb", [row], [row], 5, AS_OF)
        assert columns["diff_net_rating_ewm"] == 0.0
        assert "away_three_pa_rate_allowed" in columns


ADVANCED = {
    "offense": {"plays": 70, "drives": 12, "totalPPA": 14.0, "successRate": 0.5, "explosiveness": 1.2,
                "rushingPlays": {"ppa": 0.1}, "passingPlays": {"ppa": 0.3}, "stuffRate": 0.2, "lineYards": 3.1},
    "defense": {"plays": 60, "drives": 11, "totalPPA": 3.0, "successRate": 0.4},
}


class TestNcaafbAdvanced:
    def test_play_weighted_epa_and_points_per_drive(self):
        row = _row({}, {}, points=(35, 14), advanced=ADVANCED)
        columns = ncaafb_advanced.advanced_columns([row], [], 5, AS_OF)
        assert columns["home_off_epa_per_play"] == 0.2
        assert columns["home_def_epa_per_play"] == 0.05
        assert columns["home_off_success_rate"] == 0.5
        assert columns["home_off_pass_epa"] == 0.3
        assert math.isclose(columns["home_points_per_drive"], 35 / 12)
        assert math.isclose(columns["home_points_per_drive_allowed"], 14 / 11)
        assert columns["away_off_epa_per_play"] is None

    def test_rows_without_advanced_stats(self):
        assert ncaafb_advanced.advanced_columns([_row({}, {})], [], 5, AS_OF)["home_off_epa_per_play"] is None

    def test_season_columns(self):
        supplements = CfbdSupplements(
            talent={(2025, "1"): 900.0, (2025, "2"): 700.0},
            returning={(2025, "1"): {"percentPPA": 0.6, "usage": 0.5}},
        )
        columns = ncaafb_advanced.season_columns(supplements, 2025, "1", "2")
        assert (columns["home_talent"], columns["away_talent"], columns["diff_talent"]) == (900.0, 700.0, 200.0)
        assert columns["home_returning_ppa_pct"] == 0.6 and columns["away_returning_ppa_pct"] is None

    def test_season_columns_without_supplements(self):
        columns = ncaafb_advanced.season_columns(None, 2025, "1", "2")
        assert columns["home_talent"] is None and columns["diff_returning_ppa_pct"] is None
