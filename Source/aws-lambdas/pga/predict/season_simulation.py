"""
Monte Carlo FedEx Cup season simulation -- pure functions, no AWS/model
loading here (season_projection.py resolves the real mu/rmse inputs and
calls into this module). Each remaining event's per-golfer outcome
distribution (mu from the projected-score-to-par model, rmse from that
model's own card) is scored once up front by season_projection.py; this
module samples repeatedly from that distribution.

TOUR Championship (2025+ format): no starting-strokes handicap by seed --
simulated with the same score-sampling mechanic as every other event,
just with no points awarded (the Champion is whoever's simulated score is
lowest) and a field fixed to the top 30 by points.
"""
import random
from collections import defaultdict

from library.features.pga_fedex_cup_points import points_for_field

# Real PGA Tour Playoffs field sizes -- FedEx St. Jude Championship (top
# 70 by points), BMW Championship (top 50), TOUR Championship (top 30).
FEDEX_ST_JUDE_FIELD_SIZE = 70
BMW_CHAMPIONSHIP_FIELD_SIZE = 50
TOUR_CHAMPIONSHIP_FIELD_SIZE = 30


def simulate_event_scores(field: list[str], mu_by_golfer: dict[str, float], rmse: float, rng: random.Random) -> dict[str, float]:
    """One Monte Carlo sample of every golfer's tournament score-to-par
    for one event -- Normal(mu, rmse) per golfer, rounded to the nearest
    whole stroke. A golfer in `field` with no mu is skipped."""
    return {
        entity_id: round(rng.gauss(mu_by_golfer[entity_id], rmse))
        for entity_id in field
        if entity_id in mu_by_golfer
    }


def rank_field(scores: dict[str, float]) -> dict[str, int]:
    """entity_id -> 1-based finish_position, ascending score (lowest
    wins). Tied scores share the same position; points_for_field handles
    tie-splitting from there."""
    ordered = sorted(scores.items(), key=lambda kv: kv[1])
    positions: dict[str, int] = {}
    for i, (entity_id, score) in enumerate(ordered):
        if i > 0 and score == ordered[i - 1][1]:
            positions[entity_id] = positions[ordered[i - 1][0]]
        else:
            positions[entity_id] = i + 1
    return positions


def _top_n_by_points(points: dict[str, float], n: int) -> list[str]:
    """The top `n` golfers by accumulated points -- a tie at the cutoff
    lets both through rather than arbitrarily excluding one. Fewer than
    `n` golfers total simply returns all of them."""
    if len(points) <= n:
        return list(points)
    ordered = sorted(points.items(), key=lambda kv: -kv[1])
    cutoff_value = ordered[n - 1][1]
    return [entity_id for entity_id, value in points.items() if value >= cutoff_value]


def simulate_one_iteration(
    remaining_events: list[dict],
    fedex_st_jude: dict,
    bmw_championship: dict,
    tour_championship_mu: dict[str, float],
    tour_championship_rmse: float,
    current_points: dict[str, float],
    rng: random.Random,
) -> dict:
    """One full Monte Carlo pass through the rest of the season.

    remaining_events: chronological list of {"tier": str, "field": [...],
    "mu": {...}, "rmse": float} -- every event left before FedEx St. Jude.

    fedex_st_jude/bmw_championship: {"mu": {...}, "rmse": float} for
    their own full original field (narrowed here to the real top-70/
    top-50 cutoff by points).

    Returns {"points": {...}, "fedex_st_jude_field": [...], "bmw_field":
    [...], "tour_championship_field": [...], "champion": entity_id}."""
    points = dict(current_points)
    for event in remaining_events:
        scores = simulate_event_scores(event["field"], event["mu"], event["rmse"], rng)
        event_points = points_for_field(event["tier"], rank_field(scores))
        for entity_id, awarded in event_points.items():
            points[entity_id] = points.get(entity_id, 0) + awarded

    fedex_st_jude_field = _top_n_by_points(points, FEDEX_ST_JUDE_FIELD_SIZE)
    st_jude_scores = simulate_event_scores(fedex_st_jude_field, fedex_st_jude["mu"], fedex_st_jude["rmse"], rng)
    st_jude_points = points_for_field("fedex_st_jude", rank_field(st_jude_scores))
    for entity_id, awarded in st_jude_points.items():
        points[entity_id] = points.get(entity_id, 0) + awarded

    bmw_field = _top_n_by_points(points, BMW_CHAMPIONSHIP_FIELD_SIZE)
    bmw_scores = simulate_event_scores(bmw_field, bmw_championship["mu"], bmw_championship["rmse"], rng)
    bmw_points = points_for_field("bmw_championship", rank_field(bmw_scores))
    for entity_id, awarded in bmw_points.items():
        points[entity_id] = points.get(entity_id, 0) + awarded

    tour_championship_field = _top_n_by_points(points, TOUR_CHAMPIONSHIP_FIELD_SIZE)
    # No points for TOUR Championship itself (2025+ format) -- purely
    # decides the Champion, lowest simulated score wins outright.
    tour_championship_scores = simulate_event_scores(
        tour_championship_field, tour_championship_mu, tour_championship_rmse, rng,
    )
    champion = min(tour_championship_scores, key=tour_championship_scores.get) if tour_championship_scores else None

    return {
        "points": points,
        "fedex_st_jude_field": fedex_st_jude_field,
        "bmw_field": bmw_field,
        "tour_championship_field": tour_championship_field,
        "champion": champion,
    }


def simulate_season(
    remaining_events: list[dict],
    fedex_st_jude: dict,
    bmw_championship: dict,
    tour_championship_mu: dict[str, float],
    tour_championship_rmse: float,
    current_points: dict[str, float],
    simulations: int = 750,
    seed: int | None = None,
) -> dict:
    """Aggregates `simulations` independent calls to simulate_one_iteration
    into per-golfer probabilities. `seed` is for tests only -- the real
    scheduled run leaves it None."""
    rng = random.Random(seed)
    all_golfers = set(current_points) | {
        entity_id for event in remaining_events for entity_id in event["field"]
    } | set(fedex_st_jude.get("mu", {})) | set(bmw_championship.get("mu", {})) | set(tour_championship_mu)

    fedex_st_jude_count: dict[str, int] = defaultdict(int)
    bmw_count: dict[str, int] = defaultdict(int)
    tour_championship_count: dict[str, int] = defaultdict(int)
    champion_count: dict[str, int] = defaultdict(int)
    points_sum: dict[str, float] = defaultdict(float)

    for _ in range(simulations):
        result = simulate_one_iteration(
            remaining_events, fedex_st_jude, bmw_championship, tour_championship_mu, tour_championship_rmse,
            current_points, rng,
        )
        for entity_id in result["fedex_st_jude_field"]:
            fedex_st_jude_count[entity_id] += 1
        for entity_id in result["bmw_field"]:
            bmw_count[entity_id] += 1
        for entity_id in result["tour_championship_field"]:
            tour_championship_count[entity_id] += 1
        if result["champion"] is not None:
            champion_count[result["champion"]] += 1
        for entity_id, total in result["points"].items():
            points_sum[entity_id] += total

    standings = [
        {
            "entity_id": entity_id,
            "current_points": current_points.get(entity_id, 0.0),
            "projected_points": points_sum[entity_id] / simulations,
            "fedex_st_jude_probability": fedex_st_jude_count[entity_id] / simulations,
            "bmw_probability": bmw_count[entity_id] / simulations,
            "tour_championship_probability": tour_championship_count[entity_id] / simulations,
            "champion_probability": champion_count[entity_id] / simulations,
        }
        for entity_id in all_golfers
    ]
    standings.sort(key=lambda s: -s["projected_points"])
    return {"standings": standings, "simulations": simulations}
