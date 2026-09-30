"""
Feature columns NFL and NCAAFB build the same way: each side's identified
QB, lead rusher and lead receiver's rolling averages.
"""
from library.features.common import rolling_player_stat_averages

# (column suffix, rolling_player_stat_averages key) per position. Keys
# match the stat_line fields normalize produces: "passing_interceptions"
# is prefixed, distinct from the "interceptions" category's own bare
# "interceptions" key.
LEADER_COLUMNS = {
    "qb": (
        ("avg_passing_yards", "avg_passing_yards"),
        ("avg_passing_tds", "avg_passing_touchdowns"),
        ("avg_interceptions", "avg_passing_interceptions"),
    ),
    "rb": (
        ("avg_rushing_yards", "avg_rushing_yards"),
        ("avg_rushing_tds", "avg_rushing_touchdowns"),
    ),
    "wr": (
        ("avg_receiving_yards", "avg_receiving_yards"),
        ("avg_receiving_tds", "avg_receiving_touchdowns"),
        ("avg_receptions", "avg_receiving_receptions"),
    ),
}


def leader_columns(
    home_position_games: dict[str, list[dict] | None], away_position_games: dict[str, list[dict] | None], window: int,
) -> dict:
    """{home_qb_avg_passing_yards: ..., away_qb_...: ..., home_rb_...}
    -- per position, home then away, each ending in its games_played.
    A position with no games gets None averages and 0 games played."""
    columns = {}
    for position, stats in LEADER_COLUMNS.items():
        for side, position_games in (("home", home_position_games), ("away", away_position_games)):
            averages = rolling_player_stat_averages(position_games.get(position) or [], window)
            for column, key in stats:
                columns[f"{side}_{position}_{column}"] = averages.get(key)
            columns[f"{side}_{position}_games_played"] = averages["games_played"]
    return columns
