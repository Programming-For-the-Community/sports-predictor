"""
Monte Carlo F1 championship season simulation -- pure functions, no AWS/
model loading here (season_projection.py resolves the real mu/rmse/dnf-
probability inputs and calls into this module). Each remaining race's
per-driver finish-position outcome distribution (mu from the projected-
finish-position model, rmse from that model's own card) and DNF
probability (from the dnf-probability model) are scored once up front by
season_projection.py; this module samples repeatedly from those.

Genuinely simpler than PGA's own season_simulation.py in one way (no
field-narrowing cutoffs -- every race's field is fixed, all registered
drivers race every remaining round, unlike PGA's FedEx St. Jude/BMW/
TOUR Championship points-cutoff narrowing) and genuinely richer in two
others PGA has no analog for at all: a per-driver DNF draw before each
simulated race's scoring (a driver pulled out of that race's ranking
before points are awarded -- golf's cut/withdrawn concept only removes a
golfer from FUTURE rounds of the SAME tournament, never zeroes out an
already-decided result the way a mid-race DNF does), and a dual
driver/constructor accumulator per simulated season (see
simulate_one_iteration's own docstring for why constructor points are
DERIVED from the simulated driver points, not separately simulated).
"""
import random
from collections import defaultdict

from library.features.f1_points import constructor_points, points_for_field


def simulate_dnfs(field: list[str], dnf_probability_by_driver: dict[str, float], rng: random.Random) -> set[str]:
    """One Monte Carlo sample of which drivers in `field` DNF this race --
    an independent Bernoulli(dnf_probability) draw per driver. A driver
    with no known dnf_probability defaults to 0.0 (assumed to finish) --
    a safe, meaningful default, unlike a missing mu (simulate_race_
    finish_positions below skips a driver with no mu entirely, since
    there's no distribution to sample a finish from at all)."""
    return {
        entity_id for entity_id in field
        if rng.random() < dnf_probability_by_driver.get(entity_id, 0.0)
    }


def simulate_race_finish_positions(
    field: list[str], mu_by_driver: dict[str, float], rmse: float, dnfs: set[str], rng: random.Random,
) -> dict[str, int | None]:
    """One Monte Carlo sample of every driver's finish position for one
    race -- Normal(mu, rmse) per non-DNF, has-mu driver off the
    projected-finish-position model, then ranked ascending (lowest
    wins). Fuses PGA's own two-step simulate_event_scores + rank_field
    into one step here since F1's own sampled value (a projected finish
    position) IS already the ranking dimension, not a separate score to
    rank afterward.

    EVERY entrant in `field` is a key in the returned dict, including a
    driver with no mu or one drawn into `dnfs` -- those map to None
    rather than being omitted, matching library.features.f1_points.
    points_for_field's own expected finish_positions contract (None
    scores 0 explicitly; a key silently missing from this dict instead
    would leave that driver missing from the whole season's running
    points total entirely, not just held at 0 for this one race)."""
    scored = {
        entity_id: rng.gauss(mu_by_driver[entity_id], rmse)
        for entity_id in field
        if entity_id in mu_by_driver and entity_id not in dnfs
    }
    ordered = sorted(scored.items(), key=lambda kv: kv[1])
    positions: dict[str, int | None] = dict.fromkeys(field)
    for i, (entity_id, _) in enumerate(ordered):
        positions[entity_id] = i + 1
    return positions


def simulate_one_iteration(
    remaining_races: list[dict],
    driver_to_constructor: dict[str, str],
    current_driver_points: dict[str, float],
    rng: random.Random,
) -> dict:
    """One full Monte Carlo pass through the rest of the season -- a flat
    walk over remaining_races, no field-narrowing step (unlike PGA's own
    simulate_one_iteration): F1's field is fixed, every registered driver
    races every remaining round, so there's no Playoffs-style cutoff to
    apply between races.

    remaining_races: chronological list of {"field": [...], "mu": {...},
    "rmse": float, "dnf_probability": {...}, "sprint": bool (optional,
    defaults False -- awards the smaller sprint points table for a
    Sprint-weekend race)}.

    Returns {"driver_points": {...}, "constructor_points": {...},
    "champion": entity_id, "constructor_champion": entity_id}."""
    driver_points = dict(current_driver_points)
    for race in remaining_races:
        dnfs = simulate_dnfs(race["field"], race.get("dnf_probability", {}), rng)
        positions = simulate_race_finish_positions(race["field"], race["mu"], race["rmse"], dnfs, rng)
        race_points = points_for_field(positions, sprint=race.get("sprint", False))
        for entity_id, awarded in race_points.items():
            driver_points[entity_id] = driver_points.get(entity_id, 0.0) + awarded

    # Constructor points are fully DERIVED from the final driver_points
    # (a real sum of both a constructor's drivers' points -- see
    # library.features.f1_points.constructor_points), not tracked as a
    # separate running accumulator: current_driver_points already
    # reflects each driver's real current point tally, so summing the
    # combined current+simulated driver_points by constructor always
    # yields the correct combined constructor total on its own.
    final_constructor_points = constructor_points(driver_points, driver_to_constructor)

    champion = max(driver_points, key=driver_points.get) if driver_points else None
    constructor_champion = max(final_constructor_points, key=final_constructor_points.get) if final_constructor_points else None

    return {
        "driver_points": driver_points,
        "constructor_points": final_constructor_points,
        "champion": champion,
        "constructor_champion": constructor_champion,
    }


def simulate_season(
    remaining_races: list[dict],
    driver_to_constructor: dict[str, str],
    current_driver_points: dict[str, float],
    simulations: int = 750,
    seed: int | None = None,
) -> dict:
    """Aggregates `simulations` independent calls to simulate_one_iteration
    into per-driver AND per-constructor probabilities -- one simulated
    season produces both standings lists at once, off the same
    driver-level simulation (see simulate_one_iteration's own docstring
    for why constructor points are derived, not separately simulated).
    `seed` is for tests only -- the real scheduled run leaves it None."""
    rng = random.Random(seed)
    all_drivers = set(current_driver_points) | {
        entity_id for race in remaining_races for entity_id in race["field"]
    }
    all_constructors = {driver_to_constructor[d] for d in all_drivers if d in driver_to_constructor}
    current_constructor_points = constructor_points(current_driver_points, driver_to_constructor)

    driver_points_sum: dict[str, float] = defaultdict(float)
    driver_champion_count: dict[str, int] = defaultdict(int)
    constructor_points_sum: dict[str, float] = defaultdict(float)
    constructor_champion_count: dict[str, int] = defaultdict(int)

    for _ in range(simulations):
        result = simulate_one_iteration(remaining_races, driver_to_constructor, current_driver_points, rng)
        for entity_id, total in result["driver_points"].items():
            driver_points_sum[entity_id] += total
        if result["champion"] is not None:
            driver_champion_count[result["champion"]] += 1
        for constructor_id, total in result["constructor_points"].items():
            constructor_points_sum[constructor_id] += total
        if result["constructor_champion"] is not None:
            constructor_champion_count[result["constructor_champion"]] += 1

    driver_standings = [
        {
            "entity_id": entity_id,
            "current_points": current_driver_points.get(entity_id, 0.0),
            "projected_points": driver_points_sum[entity_id] / simulations,
            "champion_probability": driver_champion_count[entity_id] / simulations,
        }
        for entity_id in all_drivers
    ]
    driver_standings.sort(key=lambda s: -s["projected_points"])

    constructor_standings = [
        {
            "entity_id": constructor_id,
            "current_points": current_constructor_points.get(constructor_id, 0.0),
            "projected_points": constructor_points_sum[constructor_id] / simulations,
            "champion_probability": constructor_champion_count[constructor_id] / simulations,
        }
        for constructor_id in all_constructors
    ]
    constructor_standings.sort(key=lambda s: -s["projected_points"])

    return {"driver_standings": driver_standings, "constructor_standings": constructor_standings, "simulations": simulations}
