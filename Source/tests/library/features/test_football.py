"""
Unit tests for library.features.football.leader_columns.
"""
from library.features import football


def _game(**stat_line):
    return {"stat_line": stat_line}


class TestLeaderColumns:
    def test_orders_columns_by_position_then_side(self):
        columns = football.leader_columns({}, {}, 5)

        assert list(columns)[:5] == [
            "home_qb_avg_passing_yards", "home_qb_avg_passing_tds", "home_qb_avg_interceptions",
            "home_qb_games_played", "away_qb_avg_passing_yards",
        ]
        assert list(columns)[-1] == "away_wr_games_played"
        assert len(columns) == 22

    def test_averages_each_leaders_own_games(self):
        columns = football.leader_columns(
            {"qb": [_game(passing_yards=300, passing_interceptions=1), _game(passing_yards=200, passing_interceptions=0)]},
            {"wr": [_game(receiving_receptions=6)]},
            5,
        )

        assert columns["home_qb_avg_passing_yards"] == 250
        assert columns["home_qb_avg_interceptions"] == 0.5
        assert columns["home_qb_games_played"] == 2
        assert columns["away_wr_avg_receptions"] == 6
        assert columns["away_qb_avg_passing_yards"] is None
        assert columns["home_rb_games_played"] == 0
