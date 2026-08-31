"""
F1 championship season simulation -- compute Lambda side. Resolves
current-season driver/constructor standings and the remaining race
calendar from storage (the "scheduled" stub events library/normalize/
f1.py's schedule_payload_to_scheduled_events writes -- see that module's
own docstring for why F1 needs this stub at all, unlike every ESPN-backed
sport's own real schedule-sync Lambda), scores each remaining race's
fixed field via the projected-finish-position and dnf-probability models
(batched), then hands everything to season_simulation.simulate_season.
Mirrors PGA's own season_projection.py shape (weekly EventBridge ->
compute -> S3 cache).

Genuinely simpler than PGA's own module in one way: no Playoffs-style
field-narrowing cutoffs -- F1's field is fixed, every driver on the
CURRENT roster (this season's most recently raced lineup) races every
remaining round, so there's no points-cutoff field to resolve per event
the way PGA's FedEx St. Jude/BMW/TOUR Championship narrowing needs.

Deliberately does NOT simulate Sprint races (event_type "sprint") within
the season projection -- an explicit, pre-approved MVP scope cut (see
design/PROJECT_PLAN.md's F1 section: "MVP scope: ingest/store if present,
don't model it separately"), not an oversight: F1's own model set has no
continuous finish-position regression target for a Sprint session at all
(only win/podium/grid-position PROBABILITY models exist -- see model-
training/f1/train_sprint_*.py), so there's no real mu/rmse pair to sample
a Sprint race's own outcome distribution from the way there is for the
main race. Sprint points are also a small fraction of a season's total
(a handful of 8-point-max sessions against 24 races' worth of 25-point
wins). Already-BANKED sprint points (from a real, completed Sprint
session) are still fully counted in current_driver_points below, since
those come from real stored results, not a simulation.
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pandas as pd

import event_prediction
import live_features
import model_loader
import season_simulation
from library.features.f1_points import add_fastest_lap_bonus, constructor_points, points_for_field
from library.ml.model_types import ADAPTERS
from library.storage.season_projections import season_projection_key

logger = logging.getLogger("f1-predict")

SPORT = "f1"
FINISH_POSITION_MODEL_NAME = "projected-finish-position"
DNF_MODEL_NAME = "dnf-probability"
SIMULATIONS = 750


def _current_driver_points(this_season_completed_asc: list[dict]) -> dict[str, float]:
    """Walks this season's own completed events (field AND sprint,
    ascending by date) accumulating real points -- library.features.
    f1_points.points_for_field's own sprint=True argument picks the right
    table per event, and the fastest-lap bonus (field races only -- a
    real F1 rule, Sprint sessions don't award it) is applied on top."""
    points: dict[str, float] = {}
    for event in this_season_completed_asc:
        is_sprint = event.get("event_type") == "sprint"
        finish_positions = {
            p["entity_id"]: (p.get("result") or {}).get("finish_position") for p in event.get("participants", [])
        }
        for entity_id in finish_positions:
            points.setdefault(entity_id, 0.0)
        awarded = points_for_field(finish_positions, sprint=is_sprint)
        if not is_sprint:
            fastest_lap_entity_id = next(
                (p["entity_id"] for p in event.get("participants", []) if (p.get("result") or {}).get("fastest_lap")), None,
            )
            awarded = add_fastest_lap_bonus(awarded, finish_positions, fastest_lap_entity_id)
        for entity_id, value in awarded.items():
            points[entity_id] = points.get(entity_id, 0.0) + value
    return points


def _season_standings_inputs(storage) -> dict:
    """Real current-season state, entirely from already-stored data: {
    "current_driver_points": {...}, "tracked_roster": [...],
    "driver_to_constructor": {...}, "remaining_events": [event, ...]
    (chronological, "field" only), "current_season": int | None}.
    current_season is None only when no F1 event has ever been stored at
    all."""
    completed = [e for e in storage.get_all_events(SPORT, status="completed") if e.get("event_type") in ("field", "sprint")]
    scheduled = [e for e in storage.get_all_events(SPORT, status="scheduled") if e.get("event_type") == "field"]
    current_season = max(
        (e.get("season") for e in completed + scheduled if e.get("season") is not None), default=None,
    )
    if current_season is None:
        return {
            "current_driver_points": {}, "tracked_roster": [], "driver_to_constructor": {},
            "remaining_events": [], "current_season": None,
        }

    this_season_completed_asc = sorted(
        (e for e in completed if e.get("season") == current_season), key=lambda e: e.get("event_date", ""),
    )
    remaining_events = sorted(
        (e for e in scheduled if e.get("season") == current_season), key=lambda e: e.get("event_date", ""),
    )

    # The CURRENT lineup -- this season's own most recently raced "field"
    # event's own participants -- not every driver who scored ANY point
    # this season (a mid-season replacement's predecessor keeps their own
    # already-banked points in current_driver_points below, but doesn't
    # race any of the REMAINING rounds, so they're excluded from the
    # roster the simulation actually projects forward).
    most_recent_field = next(
        (e for e in reversed(this_season_completed_asc) if e.get("event_type") == "field"), None,
    )
    roster_participants = (most_recent_field or {}).get("participants", [])
    tracked_roster = [p["entity_id"] for p in roster_participants]
    driver_to_constructor = {
        p["entity_id"]: p["constructor_entity_id"] for p in roster_participants if p.get("constructor_entity_id") is not None
    }

    return {
        "current_driver_points": _current_driver_points(this_season_completed_asc),
        "tracked_roster": tracked_roster, "driver_to_constructor": driver_to_constructor,
        "remaining_events": remaining_events, "current_season": current_season,
    }


def _batch_score_drivers(estimator, model_card: dict, driver_rows: dict[str, dict]) -> dict[str, float]:
    """Scores every driver in one batched adapter.predict call, same
    ADAPTERS-direct pattern as pga/predict/season_projection.py's own
    _batch_score_golfers."""
    feature_columns = model_card["feature_columns"]
    entity_ids = list(driver_rows)
    rows = [
        {
            column: float(driver_rows[entity_id][column]) if isinstance(driver_rows[entity_id].get(column), (int, float)) else float("nan")
            for column in feature_columns
        }
        for entity_id in entity_ids
    ]
    X = pd.DataFrame(rows, columns=feature_columns, index=entity_ids)
    adapter = ADAPTERS[model_card["algorithm"]]
    predictions = adapter.predict(estimator, X)
    return dict(zip(entity_ids, (float(value) for value in predictions)))


def _score_one_remaining_race(
    storage, event: dict, tracked_roster: list[str], driver_to_constructor: dict[str, str], all_events: list[dict],
    finish_estimator, finish_model_card: dict, dnf_estimator, dnf_model_card: dict,
) -> dict:
    """{"field": [...], "mu": {...}, "dnf_probability": {...}} for one
    remaining race -- the field is the CURRENT roster, not read off the
    "scheduled" stub's own (always empty) participants."""
    driver_rows = live_features.build_projected_field_features(
        storage, SPORT, event, tracked_roster, driver_to_constructor, all_events=all_events,
    )
    mu = _batch_score_drivers(finish_estimator, finish_model_card, driver_rows)
    dnf_probability = _batch_score_drivers(dnf_estimator, dnf_model_card, driver_rows)
    return {"field": list(driver_rows), "mu": mu, "dnf_probability": dnf_probability}


def _score_remaining_races(
    storage, events: list[dict], tracked_roster: list[str], driver_to_constructor: dict[str, str], all_events: list[dict],
    finish_estimator, finish_model_card: dict, dnf_estimator, dnf_model_card: dict,
) -> list[dict]:
    """Every remaining race scored in parallel -- each is an independent
    DynamoDB-read-plus-batched-inference pass."""
    if not events:
        return []
    with ThreadPoolExecutor(max_workers=min(len(events), 8)) as executor:
        scored = list(executor.map(
            lambda event: _score_one_remaining_race(
                storage, event, tracked_roster, driver_to_constructor, all_events,
                finish_estimator, finish_model_card, dnf_estimator, dnf_model_card,
            ),
            events,
        ))
    return scored


def _final_standings_only(storage, inputs: dict) -> dict:
    """Nothing left on the calendar to simulate (season fully over).
    current_driver_points/current_constructor_points stand as projected
    outright; champion_probability is read off the real outcome (1.0/0.0)
    rather than simulated."""
    current_driver_points = inputs["current_driver_points"]
    current_constructor_points = constructor_points(current_driver_points, inputs["driver_to_constructor"])

    driver_champion = max(current_driver_points, key=current_driver_points.get) if current_driver_points else None
    constructor_champion = max(current_constructor_points, key=current_constructor_points.get) if current_constructor_points else None

    driver_standings = [
        {
            "entity_id": entity_id, "current_points": points, "projected_points": points,
            "champion_probability": 1.0 if entity_id == driver_champion else 0.0,
            **event_prediction._entity_name(storage, entity_id, "player"),
        }
        for entity_id, points in current_driver_points.items()
    ]
    driver_standings.sort(key=lambda row: -row["projected_points"])

    constructor_standings = [
        {
            "entity_id": entity_id, "current_points": points, "projected_points": points,
            "champion_probability": 1.0 if entity_id == constructor_champion else 0.0,
            **event_prediction._entity_name(storage, entity_id, "team"),
        }
        for entity_id, points in current_constructor_points.items()
    ]
    constructor_standings.sort(key=lambda row: -row["projected_points"])

    return {
        "sport": SPORT, "season": inputs["current_season"], "driver_standings": driver_standings,
        "constructor_standings": constructor_standings, "simulations": 0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def build_season_projection(storage, s3, predictions_table) -> dict | None:
    """The full F1 championship season projection (driver + constructor
    standings from one simulated pass), or None if there's no season to
    project yet (no F1 event ever stored). A missing promoted finish-
    position or dnf model raises model_loader.NoPromotedModelError,
    propagated to the caller like any other predict-side model-load
    failure."""
    inputs = _season_standings_inputs(storage)
    if inputs["current_season"] is None or not inputs["tracked_roster"]:
        return None

    if not inputs["remaining_events"]:
        return _final_standings_only(storage, inputs)

    finish_estimator, finish_model_card = model_loader.load_current_model(s3, SPORT, FINISH_POSITION_MODEL_NAME)
    dnf_estimator, dnf_model_card = model_loader.load_current_model(s3, SPORT, DNF_MODEL_NAME)
    # Completed-only: feeds prior_results/circuit_results/qualifying
    # history for the projected field's feature building.
    all_events = storage.get_all_events(SPORT, status="completed")
    rmse = finish_model_card["rmse"]

    scored_races = _score_remaining_races(
        storage, inputs["remaining_events"], inputs["tracked_roster"], inputs["driver_to_constructor"], all_events,
        finish_estimator, finish_model_card, dnf_estimator, dnf_model_card,
    )
    remaining_races_for_sim = [{**scored, "rmse": rmse} for scored in scored_races]

    simulation = season_simulation.simulate_season(
        remaining_races_for_sim, inputs["driver_to_constructor"], inputs["current_driver_points"], simulations=SIMULATIONS,
    )

    driver_standings = [
        {**row, **event_prediction._entity_name(storage, row["entity_id"], "player")}
        for row in simulation["driver_standings"]
    ]
    constructor_standings = [
        {**row, **event_prediction._entity_name(storage, row["entity_id"], "team")}
        for row in simulation["constructor_standings"]
    ]
    return {
        "sport": SPORT, "season": inputs["current_season"], "driver_standings": driver_standings,
        "constructor_standings": constructor_standings, "simulations": simulation["simulations"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def run_scheduled(storage, s3, predictions_table) -> dict:
    """Entry point for handler.py's ScheduledSeasonProjection dispatch
    (weekly EventBridge trigger). Writes the result to S3 only when
    there's a season to project."""
    result = build_season_projection(storage, s3, predictions_table)
    if result is not None:
        s3.put_json(season_projection_key(SPORT), result)
        logger.info(
            "Wrote F1 season projection: %d driver(s), %d constructor(s), season %s",
            len(result["driver_standings"]), len(result["constructor_standings"]), result["season"],
        )
    else:
        logger.info("No F1 season to project yet (no field event ever stored) -- skipping")
    return result or {"sport": SPORT, "skipped": True}
