"""
Monte Carlo FedEx Cup season simulation -- pure functions, no AWS/model
loading here (season_projection.py's own compute-Lambda side resolves
real mu/rmse inputs and calls into this module). Mirrors NBA/NCAAFB's
season_projection.py's own weekly-EventBridge -> Monte Carlo -> S3-cache
shape, but the per-iteration outcome model is genuinely different: golf
has no pairwise matchup for a cheap closed-form Elo resolution the way a
game does, so each remaining event's per-golfer outcome distribution
(mu from the projected-score-to-par model, rmse from that SAME model's
own card -- one rmse for the whole model, not per golfer) is scored ONCE
up front (real model inference, done by season_projection.py before this
module ever runs), and this module just samples repeatedly from that
already-computed distribution -- decoupling expensive inference (once
per golfer per remaining event) from cheap sampling (thousands of times
per iteration).

TOUR Championship, under the 2025+ format (confirmed live, 2026-08-28,
via the real ESPN leaderboard payload carrying no handicap/stagger field
at all, and PGA Tour's own published rule change): NO starting-strokes
handicap by seed anymore -- "the best performer over the course of four
rounds ... will win the FedExCup." So it's simulated with the EXACT same
score-sampling mechanic as every other event, just with no points
awarded (the Cup CHAMPION is simply whoever's simulated score is lowest
in that one event) and a field fixed to the top 30 by points, not scored
by tier at all.
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
    """One Monte Carlo sample of every golfer's own tournament score-to-
    par for one event -- Normal(mu, rmse) per golfer, rounded to the
    nearest whole stroke (a real score-to-par is always an integer). A
    golfer in `field` with no mu at all (shouldn't happen for a properly
    resolved field, but defends against a partial upstream resolution)
    is skipped entirely rather than sampled from a fabricated mean."""
    return {
        entity_id: round(rng.gauss(mu_by_golfer[entity_id], rmse))
        for entity_id in field
        if entity_id in mu_by_golfer
    }


def rank_field(scores: dict[str, float]) -> dict[str, int]:
    """entity_id -> 1-based finish_position, ascending score (lowest
    wins, real golf scoring). Tied scores share the SAME position (real
    PGA Tour rule) -- points_for_field's own tie-splitting handles paying
    a tie correctly from there; this function only ever needs to report
    the shared position itself."""
    ordered = sorted(scores.items(), key=lambda kv: kv[1])
    positions: dict[str, int] = {}
    for i, (entity_id, score) in enumerate(ordered):
        if i > 0 and score == ordered[i - 1][1]:
            positions[entity_id] = positions[ordered[i - 1][0]]
        else:
            positions[entity_id] = i + 1
    return positions


def _top_n_by_points(points: dict[str, float], n: int) -> list[str]:
    """The top `n` golfers by accumulated points -- a tie AT the cutoff
    (the nth and (n+1)th golfer tied on points) lets BOTH through rather
    than arbitrarily excluding one; real PGA Tour Playoffs cutoffs work
    the same way (a tie at the cutoff line advances everyone tied there).
    Fewer than `n` golfers total simply returns all of them."""
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
    "mu": {...}, "rmse": float} -- every real tour event still left to
    play BEFORE FedEx St. Jude (regular/elevated/major tier points).

    fedex_st_jude/bmw_championship: {"mu": {...}, "rmse": float} for
    their own full ORIGINAL field (this function narrows to the real top-
    70/top-50 cutoff itself, from `current_points` as it stands once
    remaining_events have all been added in).

    Returns {"points": {...} (final, post-TOUR-Championship-eligible
    total -- TOUR Championship itself awards none), "fedex_st_jude_field":
    [...], "bmw_field": [...], "tour_championship_field": [...],
    "champion": entity_id}."""
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
    into per-golfer probabilities. `seed` is for tests/reproducibility
    only -- season_projection.py's own real scheduled run leaves it None
    (a fresh random.Random() per invocation)."""
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
