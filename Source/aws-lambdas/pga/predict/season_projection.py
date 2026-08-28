"""
FedEx Cup season simulation -- compute Lambda side. Resolves current-
season standings/roster/remaining-schedule from storage, scores every
remaining event's PROJECTED field (library.features.pga_field_projection)
via live_features.build_projected_field_features plus one batched
projected-score-to-par model pass per event, then hands everything to
season_simulation.simulate_season. Mirrors NBA/NCAAFB's own
season_projection.py shape (weekly EventBridge -> compute -> S3 cache).
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pandas as pd

import event_prediction
import live_features
import model_loader
import season_simulation
from library.features.pga_fedex_cup_points import points_for_field, tier_for_event
from library.features.pga_field_projection import project_remaining_field
from library.ml.model_types import ADAPTERS
from library.storage.season_projections import season_projection_key

logger = logging.getLogger("pga-predict")

SPORT = "pga"
SCORE_MODEL_NAME = "projected-score-to-par"
SIMULATIONS = 750
# TOUR Championship's published tournament_name -- singles it out from the
# rest of the remaining schedule (no points of its own, fixed top-30-by-
# points field, decides the Champion outright). Falls back to whichever
# remaining event is chronologically last if this name is missing.
TOUR_CHAMPIONSHIP_NAME = "Tour Championship"


def _season_standings_inputs(storage) -> dict:
    """Real current-season state, entirely from already-stored data: {
    "current_points": {entity_id: float}, "tracked_roster": [entity_id,
    ...], "prior_season_events": [event, ...], "remaining_events":
    [event, ...] (chronological), "current_season": int | None}.
    current_season is None only when no PGA field event has ever been
    stored at all (never true in production, guarded for completeness)."""
    # get_all_events defaults to status="completed"; fetch both statuses
    # explicitly or remaining_events stays permanently empty, same as
    # NBA/NCAAFB/NFL's own season_projection.py.
    completed_events = [e for e in storage.get_all_events(SPORT, status="completed") if e.get("event_type") == "field"]
    scheduled_events = [e for e in storage.get_all_events(SPORT, status="scheduled") if e.get("event_type") == "field"]
    field_events = completed_events + scheduled_events
    current_season = max(
        (e.get("season") for e in field_events if e.get("season") is not None), default=None,
    )
    if current_season is None:
        return {
            "current_points": {}, "tracked_roster": [], "prior_season_events": [],
            "remaining_events": [], "current_season": None,
        }

    this_season_completed = [e for e in completed_events if e.get("season") == current_season]
    prior_season_events = [e for e in completed_events if e.get("season") == current_season - 1]
    remaining_events = sorted(
        (e for e in scheduled_events if e.get("season") == current_season),
        key=lambda e: e.get("event_date", ""),
    )

    tracked_roster = sorted({
        participant["entity_id"] for event in this_season_completed for participant in event.get("participants", [])
    })

    current_points: dict[str, float] = {entity_id: 0.0 for entity_id in tracked_roster}
    for event in this_season_completed:
        tier = tier_for_event(current_season, event.get("tournament_name"), event.get("is_major", False))
        finish_positions = {
            participant["entity_id"]: (participant.get("result") or {}).get("finish_position")
            for participant in event.get("participants", [])
        }
        for entity_id, awarded in points_for_field(tier, finish_positions).items():
            current_points[entity_id] = current_points.get(entity_id, 0.0) + awarded

    return {
        "current_points": current_points, "tracked_roster": tracked_roster,
        "prior_season_events": prior_season_events, "remaining_events": remaining_events,
        "current_season": current_season,
    }


def _split_remaining_events(remaining_events: list[dict], current_season: int) -> dict:
    """Partitions this season's remaining scheduled field events into the
    4 groups season_simulation.simulate_season needs: everything before
    the Playoffs, the FedEx St. Jude Championship, the BMW Championship,
    and the TOUR Championship. Any of the 3 special slots is None if not
    yet scheduled/ingested."""
    tour_championship = next(
        (e for e in remaining_events if e.get("tournament_name") == TOUR_CHAMPIONSHIP_NAME), None,
    )
    if tour_championship is None and remaining_events:
        tour_championship = remaining_events[-1]

    def _tier(event: dict) -> str:
        return tier_for_event(current_season, event.get("tournament_name"), event.get("is_major", False))

    fedex_st_jude = next((e for e in remaining_events if _tier(e) == "fedex_st_jude"), None)
    bmw_championship = next((e for e in remaining_events if _tier(e) == "bmw_championship"), None)

    excluded_keys = {e["event_key"] for e in (tour_championship, fedex_st_jude, bmw_championship) if e is not None}
    before_playoffs = [e for e in remaining_events if e["event_key"] not in excluded_keys]
    return {
        "before_playoffs": before_playoffs, "fedex_st_jude": fedex_st_jude,
        "bmw_championship": bmw_championship, "tour_championship": tour_championship,
    }


def _batch_score_golfers(estimator, model_card: dict, golfer_rows: dict[str, dict]) -> dict[str, float]:
    """Scores every golfer in one batched adapter.predict call, same
    ADAPTERS-direct pattern as ncaafb/predict/season_projection.py's
    _batch_score_teams. Missing/non-numeric values become NaN."""
    feature_columns = model_card["feature_columns"]
    entity_ids = list(golfer_rows)
    rows = [
        {
            column: float(golfer_rows[entity_id][column]) if isinstance(golfer_rows[entity_id].get(column), (int, float)) else float("nan")
            for column in feature_columns
        }
        for entity_id in entity_ids
    ]
    X = pd.DataFrame(rows, columns=feature_columns, index=entity_ids)
    adapter = ADAPTERS[model_card["algorithm"]]
    predictions = adapter.predict(estimator, X)
    return dict(zip(entity_ids, (float(value) for value in predictions)))


def _score_one_remaining_event(
    storage, event: dict, prior_season_events: list[dict], tracked_roster: list[str],
    all_events: list[dict], estimator, model_card: dict, current_season: int,
) -> dict:
    """{"tier": str, "field": [entity_id, ...], "mu": {entity_id: float}}
    for one remaining event -- the field is PROJECTED, not read off the
    event's own sparse/empty stored participants."""
    field = project_remaining_field(event, prior_season_events, tracked_roster)
    golfer_rows = live_features.build_projected_field_features(
        storage, SPORT, event, field, history_events=all_events,
    )
    mu = _batch_score_golfers(estimator, model_card, golfer_rows)
    tier = tier_for_event(current_season, event.get("tournament_name"), event.get("is_major", False))
    return {"tier": tier, "field": list(mu), "mu": mu}


def _score_remaining_events(
    storage, events: list[dict], prior_season_events: list[dict], tracked_roster: list[str],
    all_events: list[dict], estimator, model_card: dict, current_season: int,
) -> dict[str, dict]:
    """{event_key: scored} for every event in `events`, in parallel --
    each is an independent DynamoDB-read-plus-batched-inference pass."""
    if not events:
        return {}
    with ThreadPoolExecutor(max_workers=min(len(events), 8)) as executor:
        scored = list(executor.map(
            lambda event: (
                event["event_key"],
                _score_one_remaining_event(
                    storage, event, prior_season_events, tracked_roster, all_events, estimator, model_card, current_season,
                ),
            ),
            events,
        ))
    return dict(scored)


def _final_standings_only(storage, inputs: dict) -> dict:
    """Nothing left on the calendar to simulate (season fully over).
    current_points stand as projected_points outright; the 3 Playoffs-
    field probabilities and champion_probability are read off real
    outcomes (1.0/0.0) rather than simulated."""
    current_season = inputs["current_season"]
    all_completed_this_season = [
        e for e in storage.get_all_events(SPORT, status="completed")
        if e.get("event_type") == "field" and e.get("season") == current_season
    ]

    def _find(tier_name: str) -> dict | None:
        return next(
            (e for e in all_completed_this_season
             if tier_for_event(current_season, e.get("tournament_name"), e.get("is_major", False)) == tier_name),
            None,
        )

    fedex_st_jude = _find("fedex_st_jude")
    bmw_championship = _find("bmw_championship")
    tour_championship = next(
        (e for e in all_completed_this_season if e.get("tournament_name") == TOUR_CHAMPIONSHIP_NAME), None,
    )

    def _participant_ids(event: dict | None) -> set:
        return {p["entity_id"] for p in event.get("participants", [])} if event is not None else set()

    fedex_st_jude_field = _participant_ids(fedex_st_jude)
    bmw_field = _participant_ids(bmw_championship)
    tour_championship_field = _participant_ids(tour_championship)
    champion = None
    if tour_championship is not None:
        champion = next(
            (p["entity_id"] for p in tour_championship.get("participants", []) if (p.get("result") or {}).get("finish_position") == 1),
            None,
        )

    standings = []
    for entity_id in inputs["tracked_roster"]:
        standings.append({
            "entity_id": entity_id,
            "current_points": inputs["current_points"].get(entity_id, 0.0),
            "projected_points": inputs["current_points"].get(entity_id, 0.0),
            "fedex_st_jude_probability": 1.0 if entity_id in fedex_st_jude_field else 0.0,
            "bmw_probability": 1.0 if entity_id in bmw_field else 0.0,
            "tour_championship_probability": 1.0 if entity_id in tour_championship_field else 0.0,
            "champion_probability": 1.0 if entity_id == champion else 0.0,
            **event_prediction._golfer_name(storage, entity_id),
        })
    standings.sort(key=lambda row: -row["projected_points"])
    return {
        "sport": SPORT, "season": current_season, "standings": standings, "simulations": 0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def build_season_projection(storage, s3, predictions_table) -> dict | None:
    """The full FedEx Cup season projection, or None if there's no season
    to project yet (no PGA field event ever stored). A missing promoted
    score model raises model_loader.NoPromotedModelError, propagated to
    the caller like any other predict-side model-load failure."""
    inputs = _season_standings_inputs(storage)
    if inputs["current_season"] is None or not inputs["tracked_roster"]:
        return None

    split = _split_remaining_events(inputs["remaining_events"], inputs["current_season"])
    special_events = [e for e in (split["fedex_st_jude"], split["bmw_championship"], split["tour_championship"]) if e is not None]
    all_remaining = split["before_playoffs"] + special_events

    if not all_remaining:
        return _final_standings_only(storage, inputs)

    estimator, model_card = model_loader.load_current_model(s3, SPORT, SCORE_MODEL_NAME)
    # Completed-only: feeds prior_results/course_results for the
    # projected field's feature building.
    all_events = storage.get_all_events(SPORT, status="completed")
    rmse = model_card["rmse"]

    scored_by_key = _score_remaining_events(
        storage, all_remaining, inputs["prior_season_events"], inputs["tracked_roster"], all_events,
        estimator, model_card, inputs["current_season"],
    )

    remaining_events_for_sim = [scored_by_key[e["event_key"]] for e in split["before_playoffs"]]
    fedex_st_jude_scored = scored_by_key.get(split["fedex_st_jude"]["event_key"]) if split["fedex_st_jude"] else {"mu": {}}
    bmw_scored = scored_by_key.get(split["bmw_championship"]["event_key"]) if split["bmw_championship"] else {"mu": {}}
    tour_championship_scored = scored_by_key.get(split["tour_championship"]["event_key"]) if split["tour_championship"] else {"mu": {}}

    simulation = season_simulation.simulate_season(
        remaining_events_for_sim,
        {"mu": fedex_st_jude_scored["mu"], "rmse": rmse},
        {"mu": bmw_scored["mu"], "rmse": rmse},
        tour_championship_scored["mu"], rmse,
        inputs["current_points"],
        simulations=SIMULATIONS,
    )

    standings = [
        {**row, **event_prediction._golfer_name(storage, row["entity_id"])}
        for row in simulation["standings"]
    ]
    return {
        "sport": SPORT, "season": inputs["current_season"], "standings": standings,
        "simulations": simulation["simulations"], "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def run_scheduled(storage, s3, predictions_table) -> dict:
    """Entry point for handler.py's ScheduledSeasonProjection dispatch
    (weekly EventBridge trigger). Writes the result to S3 only when
    there's a season to project."""
    result = build_season_projection(storage, s3, predictions_table)
    if result is not None:
        s3.put_json(season_projection_key(SPORT), result)
        logger.info("Wrote PGA season projection: %d golfers, season %s", len(result["standings"]), result["season"])
    else:
        logger.info("No PGA season to project yet (no field event ever stored) -- skipping")
    return result or {"sport": SPORT, "skipped": True}
