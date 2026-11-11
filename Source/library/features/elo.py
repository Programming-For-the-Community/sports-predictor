"""
Per-sport Elo constants and the sweep that checks them. Every team-sport
caller rates through sport_elo_ratings so training, live serving and the
season simulations share one rating scale per sport. NHL keeps its own
franchise-keyed ratings (library.features.nhl_dataset).
"""
import itertools
import math
from dataclasses import asdict, dataclass, replace

from library.features.common import (
    MOV_LOG,
    MOV_POWER,
    compute_elo_ratings,
    expected_score,
)


@dataclass(frozen=True)
class EloParams:
    k_factor: float
    home_advantage: float
    season_carryover: float
    mov_formula: str
    mov_base: float
    mov_divisor: float


_FOOTBALL = EloParams(k_factor=20.0, home_advantage=55.0, season_carryover=2 / 3, mov_formula=MOV_LOG, mov_base=2.2, mov_divisor=0.001)
_BASKETBALL = EloParams(k_factor=20.0, home_advantage=100.0, season_carryover=0.75, mov_formula=MOV_POWER, mov_base=7.5, mov_divisor=0.006)

SPORT_ELO = {
    "nfl": _FOOTBALL,
    "ncaafb": _FOOTBALL,
    "nba": _BASKETBALL,
    "ncaambb": _BASKETBALL,
}

SWEEP_GRID = {
    "k_factor": (10.0, 15.0, 20.0, 25.0, 30.0, 40.0),
    "home_advantage": (25.0, 50.0, 75.0, 100.0, 125.0),
    "season_carryover": (0.5, 2 / 3, 0.75, 0.85),
}


def params_for(sport: str) -> EloParams:
    return SPORT_ELO[sport]


def sport_elo_ratings(sport: str, events: list[dict], as_of_season: int | None = None) -> tuple[dict, dict]:
    """compute_elo_ratings with the sport's own constants."""
    return compute_elo_ratings(events, as_of_season=as_of_season, **asdict(params_for(sport)))


def _home_won(event: dict) -> float | None:
    scores = {p.get("role"): (p.get("result") or {}).get("score") for p in event.get("participants", [])}
    home, away = scores.get("home"), scores.get("away")
    if home is None or away is None:
        return None
    return 0.5 if home == away else float(home > away)


def elo_log_loss(events: list[dict], params: EloParams) -> float | None:
    """Mean log loss of the pre-game Elo home-win chance, over every scored
    event after each event's first season in the data (that season only
    seeds the ratings)."""
    pre_game, _ = compute_elo_ratings(events, **asdict(params))
    first_season = min((e.get("season") for e in events if e.get("season") is not None), default=None)
    losses = []
    for event in events:
        outcome, ratings = _home_won(event), pre_game.get(event.get("event_key"), {})
        if outcome is None or "home_pre_rating" not in ratings or event.get("season") == first_season:
            continue
        advantage = 0.0 if event.get("neutral_site") else params.home_advantage
        chance = min(max(expected_score(ratings["home_pre_rating"], ratings["away_pre_rating"], advantage), 1e-6), 1 - 1e-6)
        losses.append(-(outcome * math.log(chance) + (1 - outcome) * math.log(1 - chance)))
    return sum(losses) / len(losses) if losses else None


def sweep(sport: str, events: list[dict], grid: dict[str, tuple] = SWEEP_GRID) -> list[tuple[float, EloParams]]:
    """(log loss, params) for the sport's constants with every grid
    combination swapped in, best first. Changes nothing -- the feature job
    logs it so the constants above can be updated by hand."""
    base = params_for(sport)
    results = []
    for values in itertools.product(*grid.values()):
        params = replace(base, **dict(zip(grid, values)))
        loss = elo_log_loss(events, params)
        if loss is not None:
            results.append((loss, params))
    return sorted(results, key=lambda pair: pair[0])


def log_sweep(sport: str, events: list[dict], logger, top: int = 5) -> None:
    current = elo_log_loss(events, params_for(sport))
    ranked = sweep(sport, events)
    logger.info("Elo sweep (%s): current constants log loss %s over %d events", sport, current, len(events))
    for loss, params in ranked[:top]:
        logger.info("Elo sweep (%s): log loss %.5f with %s", sport, loss, asdict(params))
