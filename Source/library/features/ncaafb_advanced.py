"""
NCAAFB columns from CFBD's supplements (library.storage.cfbd_supplements):
rolling EPA, success rate, explosiveness, line play and points per drive
from each paired box row's "advanced" entry, plus each team's season
talent composite and returning production.

EPA and success rate are play-weighted (totalPPA / plays; successRate x
plays / plays); the per-split and line-play rates are game means.
"""
from library.features.box_windows import CORE, Metric, MetricSet, number, sided

_RETURNING_FIELDS = {
    "returning_ppa_pct": "percentPPA",
    "returning_pass_ppa_pct": "percentPassingPPA",
    "returning_rush_ppa_pct": "percentRushingPPA",
    "returning_receiving_ppa_pct": "percentReceivingPPA",
    "returning_usage": "usage",
}


def _unit(side: str, *path: str):
    def value(row: dict) -> float | None:
        node = (row.get("advanced") or {}).get(side)
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        return number(node)
    return value


def _weighted(side: str, rate_key: str):
    def value(row: dict) -> float | None:
        rate, plays = _unit(side, rate_key)(row), _unit(side, "plays")(row)
        return None if rate is None or plays is None else rate * plays
    return value


def _points(key: str):
    return lambda row: number(row.get(key))


def _metrics() -> list[Metric]:
    metrics = []
    for side, prefix in (("offense", "off"), ("defense", "def")):
        metrics += [
            Metric(f"{prefix}_epa_per_play", _unit(side, "totalPPA"), _unit(side, "plays"), windows=CORE),
            Metric(f"{prefix}_success_rate", _weighted(side, "successRate"), _unit(side, "plays")),
            Metric(f"{prefix}_explosiveness", _unit(side, "explosiveness")),
            Metric(f"{prefix}_rush_epa", _unit(side, "rushingPlays", "ppa")),
            Metric(f"{prefix}_pass_epa", _unit(side, "passingPlays", "ppa")),
            Metric(f"{prefix}_stuff_rate", _unit(side, "stuffRate")),
            Metric(f"{prefix}_line_yards", _unit(side, "lineYards")),
        ]
    metrics += [
        Metric("points_per_drive", _points("points_for"), _unit("offense", "drives"), windows=CORE),
        Metric("points_per_drive_allowed", _points("points_against"), _unit("defense", "drives"), windows=CORE),
    ]
    return metrics


_SET = MetricSet("ncaafb_advanced", _metrics())
_DIFF_NAMES = [f"{n}_{w}" if w else n for n in ("off_epa_per_play", "def_epa_per_play", "points_per_drive") for w in CORE]


def advanced_columns(home_rows: list[dict], away_rows: list[dict], window: int, as_of: str | None) -> dict:
    return sided(_SET.columns(home_rows, window, as_of), _SET.columns(away_rows, window, as_of), _DIFF_NAMES)


def _season_side(supplements, season: int | None, team_id: str) -> dict:
    returning = supplements.returning.get((season, team_id)) or {}
    return {
        "talent": supplements.talent.get((season, team_id)),
        **{name: number(returning.get(field)) for name, field in _RETURNING_FIELDS.items()},
    }


def season_columns(supplements, season: int | None, home_id: str, away_id: str) -> dict:
    """Talent composite and returning production for both sides; None
    without supplements."""
    if supplements is None:
        empty = {"talent": None, **dict.fromkeys(_RETURNING_FIELDS)}
        home = away = empty
    else:
        home, away = _season_side(supplements, season, home_id), _season_side(supplements, season, away_id)
    return sided(home, away, ["talent", "returning_ppa_pct"])
