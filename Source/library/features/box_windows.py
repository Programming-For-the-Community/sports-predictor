"""
Rolling box-score metrics over a paired box history (library.features.
box_history, most recent first). A metric is a numerator and optional
denominator per game; over a window its value is the ratio of the sums
(a plain mean without a denominator), skipping games missing either part.

Windows: BASE (the caller's rolling window, column named after the metric
alone), LAST10, SEASON (this season's games, after the last off-season
gap) and EWM (every game given, each EWM_HALF_LIFE_GAMES games older
weighing half as much).
"""
from collections.abc import Callable
from dataclasses import dataclass

from library.features.common import EWM_HALF_LIFE_GAMES, games_this_season

BASE, LAST10, SEASON, EWM = "", "last10", "season_to_date", "ewm"
CORE = (BASE, LAST10, SEASON, EWM)
STANDARD = (BASE, SEASON)
EXTENDED = (LAST10, SEASON, EWM)

Value = Callable[[dict], float | None]


def number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def own_line(row: dict) -> dict:
    return row.get("stat_line") or {}


def opp_line(row: dict) -> dict:
    """The opponent's row of the same game; empty on an unpaired row."""
    return row.get("opponent_stat_line") or {}


def own(key: str) -> Value:
    return lambda row: number(own_line(row).get(key))


def opp(key: str) -> Value:
    return lambda row: number(opp_line(row).get(key))


def plus(*parts: Value) -> Value:
    def value(row: dict) -> float | None:
        values = [part(row) for part in parts]
        return None if any(v is None for v in values) else sum(values)
    return value


def _flip(row: dict) -> dict:
    return {
        **row,
        "stat_line": opp_line(row), "opponent_stat_line": own_line(row),
        "points_for": row.get("points_against"), "points_against": row.get("points_for"),
    }


@dataclass(frozen=True)
class Metric:
    name: str
    numerator: Value
    denominator: Value | None = None
    scale: float = 1.0
    windows: tuple[str, ...] = STANDARD

    def allowed(self, name: str | None = None, windows: tuple[str, ...] | None = None) -> "Metric":
        """The same measure of the opponent's side of each game."""
        denominator = self.denominator
        return Metric(
            name or f"{self.name}_allowed",
            lambda row: self.numerator(_flip(row)),
            (lambda row: denominator(_flip(row))) if denominator else None,
            self.scale,
            windows or self.windows,
        )

    def part(self, row: dict) -> tuple[float, float] | None:
        a = self.numerator(row)
        b = self.denominator(row) if self.denominator else 1.0
        return None if a is None or b is None else (a, b)


def _column(name: str, window: str) -> str:
    return f"{name}_{window}" if window else name


class MetricSet:
    def __init__(self, key: str, metrics: list[Metric]):
        self.key = key
        self.metrics = metrics

    def _parts(self, row: dict) -> list[tuple[float, float] | None]:
        """Each metric's per-game parts, cached on the row (a history row is
        reused for every later game it precedes)."""
        cache = row.setdefault("_metric_parts", {})
        if self.key not in cache:
            cache[self.key] = [metric.part(row) for metric in self.metrics]
        return cache[self.key]

    def columns(self, rows: list[dict], window: int, as_of: str | None) -> dict:
        parts = [self._parts(row) for row in rows]
        weights = {
            BASE: [1.0] * min(window, len(rows)),
            LAST10: [1.0] * min(10, len(rows)),
            SEASON: [1.0] * (games_this_season(rows, as_of) or 0),
            EWM: [0.5 ** (age / EWM_HALF_LIFE_GAMES) for age in range(len(rows))],
        }
        columns = {}
        for i, metric in enumerate(self.metrics):
            for window_name in metric.windows:
                num = den = 0.0
                for row_parts, weight in zip(parts, weights[window_name]):
                    if row_parts[i] is not None:
                        num += weight * row_parts[i][0]
                        den += weight * row_parts[i][1]
                columns[_column(metric.name, window_name)] = metric.scale * num / den if den else None
        return columns


def differences(columns: dict, name: str, plus_name: str, minus_name: str, windows: tuple[str, ...]) -> dict:
    """name_<window> = plus_name_<window> - minus_name_<window>."""
    result = {}
    for window_name in windows:
        a, b = columns.get(_column(plus_name, window_name)), columns.get(_column(minus_name, window_name))
        result[_column(name, window_name)] = a - b if a is not None and b is not None else None
    return result


def sided(home: dict, away: dict, diff_names: list[str]) -> dict:
    """home_/away_ prefixed columns plus diff_ (home minus away) for diff_names."""
    columns = {**{f"home_{k}": v for k, v in home.items()}, **{f"away_{k}": v for k, v in away.items()}}
    for name in diff_names:
        a, b = home.get(name), away.get(name)
        columns[f"diff_{name}"] = a - b if a is not None and b is not None else None
    return columns


def core_names(metric_names: list[str]) -> list[str]:
    return [_column(name, window) for name in metric_names for window in CORE]
