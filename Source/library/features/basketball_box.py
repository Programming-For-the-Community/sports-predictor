"""
Basketball pace, ratings and four-factor columns from a paired box history
(library.features.box_history), for NBA and NCAA MBB.

A game's possessions are the mean of both sides' estimates (FGA - ORB +
TOV + 0.44*FTA, turnovers including team turnovers). Pace is possessions
per regulation game: a row's `periods` (set from the scoreboard's
linescores) adds five minutes per overtime; without it a game counts as
regulation length.
"""
from library.features.box_windows import (
    CORE,
    EXTENDED,
    Metric,
    MetricSet,
    differences,
    number,
    opp,
    opp_line,
    own,
    own_line,
    sided,
)

# sport -> (regulation periods, regulation minutes)
REGULATION = {"nba": (4, 48.0), "ncaambb": (2, 40.0)}
OVERTIME_MINUTES = 5.0

_CORE_NAMES = ["pace", "off_rating", "def_rating", "net_rating", "efg_pct", "efg_pct_allowed", "tov_pct", "tov_pct_forced", "orb_pct", "drb_pct"]


def _turnovers(line: dict) -> float | None:
    total = number(line.get("total_turnovers"))
    return total if total is not None else number(line.get("turnovers"))


def _side_possessions(line: dict) -> float | None:
    parts = [number(line.get("field_goal_attempts")), number(line.get("offensive_rebounds")), _turnovers(line), number(line.get("free_throw_attempts"))]
    if any(p is None for p in parts):
        return None
    fga, orb, tov, fta = parts
    return fga - orb + tov + 0.44 * fta


def possessions(row: dict) -> float | None:
    estimates = [p for p in (_side_possessions(own_line(row)), _side_possessions(opp_line(row))) if p is not None]
    return sum(estimates) / len(estimates) if estimates else None


def _game_minutes(sport: str):
    regulation_periods, regulation_minutes = REGULATION[sport]

    def minutes(row: dict) -> float:
        periods = number(row.get("periods"))
        overtimes = max(0.0, periods - regulation_periods) if periods is not None else 0.0
        return regulation_minutes + OVERTIME_MINUTES * overtimes
    return minutes


def _pace(sport: str) -> Metric:
    regulation_minutes = REGULATION[sport][1]

    def scaled_possessions(row: dict) -> float | None:
        p = possessions(row)
        return None if p is None else p * regulation_minutes
    return Metric("pace", scaled_possessions, _game_minutes(sport), windows=CORE)


def _effective_makes(row: dict) -> float | None:
    made, threes = own("field_goals_made")(row), own("three_pointers_made")(row)
    return None if made is None or threes is None else made + 0.5 * threes


def _shot_possessions(row: dict) -> float | None:
    fga, fta, tov = own("field_goal_attempts")(row), own("free_throw_attempts")(row), _turnovers(own_line(row))
    return None if fga is None or fta is None or tov is None else fga + 0.44 * fta + tov


def _rebound_share(name: str, own_key: str, opp_key: str) -> Metric:
    def denominator(row: dict) -> float | None:
        a, b = own(own_key)(row), opp(opp_key)(row)
        return None if a is None or b is None else a + b
    return Metric(name, own(own_key), denominator, windows=CORE)


def _per_100(name: str, key: str) -> Metric:
    return Metric(name, own(key), possessions, scale=100.0)


def _metrics(sport: str) -> list[Metric]:
    efg = Metric("efg_pct", _effective_makes, own("field_goal_attempts"), windows=CORE)
    tov = Metric("tov_pct", lambda row: _turnovers(own_line(row)), _shot_possessions, windows=CORE)
    ft_rate = Metric("ft_rate", own("free_throws_made"), own("field_goal_attempts"))
    three_rate = Metric("three_pa_rate", own("three_point_attempts"), own("field_goal_attempts"))
    paint, fast_break, off_turnovers = (
        _per_100("paint_pts_per_100", "points_in_paint"),
        _per_100("fast_break_pts_per_100", "fast_break_points"),
        _per_100("pts_off_tov_per_100", "turnover_points"),
    )
    return [
        _pace(sport),
        Metric("off_rating", lambda row: number(row.get("points_for")), possessions, scale=100.0, windows=CORE),
        Metric("def_rating", lambda row: number(row.get("points_against")), possessions, scale=100.0, windows=CORE),
        efg, efg.allowed(),
        tov, tov.allowed("tov_pct_forced"),
        _rebound_share("orb_pct", "offensive_rebounds", "defensive_rebounds"),
        _rebound_share("drb_pct", "defensive_rebounds", "offensive_rebounds"),
        ft_rate, ft_rate.allowed(),
        three_rate, three_rate.allowed(),
        paint, paint.allowed(), fast_break, fast_break.allowed(), off_turnovers, off_turnovers.allowed(),
        Metric("field_goal_pct", own("field_goals_made"), own("field_goal_attempts"), windows=EXTENDED),
        Metric("three_point_pct", own("three_pointers_made"), own("three_point_attempts"), windows=EXTENDED),
        Metric("free_throw_pct", own("free_throws_made"), own("free_throw_attempts"), windows=EXTENDED),
        *(Metric(f"avg_{stat}", own(stat), windows=EXTENDED) for stat in ("assists", "steals", "blocks", "turnovers")),
    ]


_SETS = {sport: MetricSet(f"basketball_{sport}", _metrics(sport)) for sport in REGULATION}


def side_columns(sport: str, rows: list[dict], window: int, as_of: str | None) -> dict:
    columns = _SETS[sport].columns(rows, window, as_of)
    columns.update(differences(columns, "net_rating", "off_rating", "def_rating", CORE))
    return columns


def basketball_box_columns(sport: str, home_rows: list[dict], away_rows: list[dict], window: int, as_of: str | None) -> dict:
    """home_/away_/diff_ columns. Rows: each side's paired box history,
    most recent first, not including this game."""
    diff_names = [f"{n}_{w}" if w else n for n in _CORE_NAMES for w in CORE]
    return sided(side_columns(sport, home_rows, window, as_of), side_columns(sport, away_rows, window, as_of), diff_names)
