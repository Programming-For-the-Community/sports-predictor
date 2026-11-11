"""
Football efficiency columns from a paired box history (library.features.
box_history), for NFL (ESPN box scores) and NCAAFB (CFBD box scores).

Plays are ESPN's total_offensive_plays where present, else pass attempts
+ rushes + sacks taken. Sacks taken are ESPN's sacks_taken, else (CFBD,
which only reports a defence's sacks) the opponent's sacks. NFL alone has
drives and red-zone trips.
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

_CORE_NAMES = ["yards_per_play", "yards_per_play_allowed", "net_yards_per_play", "turnover_margin", "sack_rate", "pressure_rate", "third_down_pct", "third_down_pct_allowed"]


def _sacks_taken(row: dict) -> float | None:
    taken = number(own_line(row).get("sacks_taken"))
    return taken if taken is not None else number(opp_line(row).get("sacks"))


def _plays(row: dict) -> float | None:
    line = own_line(row)
    plays = number(line.get("total_offensive_plays"))
    if plays is not None:
        return plays
    parts = [number(line.get("pass_attempts")), number(line.get("rushing_attempts")), _sacks_taken(row)]
    return None if any(p is None for p in parts) else sum(parts)


def _dropbacks(row: dict) -> float | None:
    attempts, sacks = number(own_line(row).get("pass_attempts")), _sacks_taken(row)
    return None if attempts is None or sacks is None else attempts + sacks


def _margin(row: dict) -> float | None:
    forced, lost = opp("turnovers")(row), own("turnovers")(row)
    return None if forced is None or lost is None else forced - lost


def _ratio(name: str, numerator: str, denominator: str, windows=None) -> Metric:
    return Metric(name, own(numerator), own(denominator), windows=windows or ("", "season_to_date"))


def _metrics(nfl: bool) -> list[Metric]:
    yards_per_play = Metric("yards_per_play", own("total_yards"), _plays, windows=CORE)
    sack_rate = Metric("sack_rate", _sacks_taken, _dropbacks, windows=CORE)
    third_down = _ratio("third_down_pct", "third_down_conversions", "third_down_attempts", EXTENDED)
    yards_per_pass = _ratio("yards_per_pass_attempt", "net_passing_yards", "pass_attempts")
    yards_per_rush = _ratio("yards_per_rush", "rushing_yards", "rushing_attempts")
    first_downs = Metric("first_downs_per_play", own("first_downs"), _plays)
    metrics = [
        yards_per_play, yards_per_play.allowed(),
        yards_per_pass, yards_per_pass.allowed(),
        yards_per_rush, yards_per_rush.allowed(),
        first_downs, first_downs.allowed(),
        Metric("turnover_margin", _margin, windows=CORE),
        Metric("takeaways", opp("turnovers")),
        sack_rate, sack_rate.allowed("pressure_rate"),
        third_down, Metric("third_down_pct_allowed", opp("third_down_conversions"), opp("third_down_attempts"), windows=CORE),
        Metric("fourth_down_attempts", own("fourth_down_attempts")),
        _ratio("fourth_down_pct", "fourth_down_conversions", "fourth_down_attempts"),
        Metric("avg_turnovers", own("turnovers"), windows=EXTENDED),
        Metric("avg_total_yards", own("total_yards"), windows=EXTENDED),
        Metric("avg_possession_time_seconds", own("possession_time_seconds"), windows=EXTENDED),
        Metric("avg_penalties", own("penalties"), windows=EXTENDED),
        Metric("avg_penalty_yards", own("penalty_yards"), windows=EXTENDED),
    ]
    if nfl:
        points_per_drive = Metric("points_per_drive", lambda row: number(row.get("points_for")), own("total_drives"), windows=CORE)
        red_zone = _ratio("red_zone_pct", "red_zone_conversions", "red_zone_attempts", EXTENDED)
        metrics += [
            points_per_drive, points_per_drive.allowed(),
            red_zone, Metric("red_zone_pct_allowed", opp("red_zone_conversions"), opp("red_zone_attempts")),
            Metric("red_zone_trips", own("red_zone_attempts")),
        ]
    return metrics


_SETS = {"nfl": MetricSet("football_nfl", _metrics(nfl=True)), "ncaafb": MetricSet("football_ncaafb", _metrics(nfl=False))}


def _side(sport: str, rows: list[dict], window: int, as_of: str | None) -> dict:
    columns = _SETS[sport].columns(rows, window, as_of)
    columns.update(differences(columns, "net_yards_per_play", "yards_per_play", "yards_per_play_allowed", CORE))
    return columns


def football_box_columns(sport: str, home_rows: list[dict], away_rows: list[dict], window: int, as_of: str | None) -> dict:
    """home_/away_/diff_ efficiency columns. Rows: each side's paired box
    history, most recent first, not including this game."""
    diff_names = [f"{n}_{w}" if w else n for n in _CORE_NAMES for w in CORE]
    if sport == "nfl":
        diff_names += [f"points_per_drive_{w}" if w else "points_per_drive" for w in CORE]
    home, away = _side(sport, home_rows, window, as_of), _side(sport, away_rows, window, as_of)
    # The base-window third-down rate already ships as {side}_third_down_pct.
    return sided(home, away, [n for n in diff_names if n != "third_down_pct"])
