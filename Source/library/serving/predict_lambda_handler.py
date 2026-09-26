"""
Shared dispatch body for every sport's predict Lambda (the background
compute worker behind ScheduledSeasonProjection/ComputeAndCachePrediction
-- never invoked by API Gateway directly). Confirmed identical control
flow across all 6 sports, differing only in:
  - run_scheduled's own argument list (nfl/nba/ncaafb take (storage,
    model_bucket, predictions_table); pga/f1 take (storage, model_bucket)
    -- no predictions_table; ncaambb takes those three plus its own
    raw_bucket for conference-membership lookups), and
  - whether a "player_prop" route exists at all (pga/f1 have none -- one
    compute already scores the whole field/grid).

Rather than force one fixed argument shape onto run_scheduled/
compute_and_cache_event/compute_and_cache_player_prop, each per-sport
handler.py passes its own zero/one-arg BOUND closure capturing whichever
local singleton getters it personally needs (e.g. `lambda: season_
projection.run_scheduled(_get_storage(), _get_model_bucket())` for pga,
vs. nfl's 3-arg version) -- this module only owns the dispatch shape
(which branch fires for which event shape), not each sport's own
argument list.

Each bound closure still references its sport's own event_prediction/
season_projection MODULE by name inside the closure body (e.g.
`event_prediction.compute_and_cache_event(...)`), resolved via attribute
lookup at CALL time -- so patch.object(event_prediction, "compute_and_
cache_event", ...) in tests (which patches that shared module object's
own __dict__, the same object this closure looks up) still takes effect
regardless of which file's closure runs it. Confirmed by reading every
sport's own test_predict_routing.py/test_predict_handler.py before
writing this, same discipline the normalize-dispatch and predict-read
dedup passes both used after finding the equivalent pitfall in each.

SnapshotPrediction ({"detail-type": "SnapshotPrediction", "event_id": ...}) is
handled only when a sport passes snapshot_event_fn: one fresh compute, then a
copy of what it recorded to the event's immutable pre-kickoff snapshot.

The two sport "shapes" for ComputeAndCachePrediction, preserved exactly:
  - nfl/nba/ncaafb/ncaambb (has_player_prop_route=True): ANY
    ComputeAndCachePrediction detail-type returns {"status": "ok"}
    unconditionally, even for an unrecognized route (no compute call
    happens, but it's still treated as a handled invocation) -- matches
    each of these sports' own original handler.py exactly.
  - pga/f1 (has_player_prop_route=False): only route == "event" is
    handled; anything else (including a route key that isn't "event")
    falls through to the unrecognized-invocation branch below, matching
    each of these sports' own original combined `and` condition exactly.
"""
from library.logging_safety import safe_log_value


def make_lambda_handler(
    *, warmup_fn, run_scheduled_fn, compute_and_cache_event_fn, logger,
    compute_and_cache_player_prop_fn=None, snapshot_event_fn=None,
):
    def lambda_handler(event, context):
        # EventBridge Scheduler warmup ping -- keeps a container past its
        # own (slow, xgboost/pandas/sklearn-heavy) cold-start import chain.
        if event.get("warmup"):
            return warmup_fn()

        if event.get("detail-type") == "ScheduledSeasonProjection":
            return run_scheduled_fn()
        # From the prediction-scheduler shortly before kickoff -- only the
        # sports that pass snapshot_event_fn take part.
        if event.get("detail-type") == "SnapshotPrediction" and snapshot_event_fn is not None:
            return {"status": "ok", "snapshotted": snapshot_event_fn(event["event_id"])}

        if compute_and_cache_player_prop_fn is not None:
            if event.get("detail-type") == "ComputeAndCachePrediction":
                route = event.get("route")
                if route == "event":
                    compute_and_cache_event_fn(event["event_id"])
                elif route == "player_prop":
                    compute_and_cache_player_prop_fn(event["event_id"], event["entity_id"], event["stat"])
                return {"status": "ok"}
        elif event.get("detail-type") == "ComputeAndCachePrediction" and event.get("route") == "event":
            compute_and_cache_event_fn(event["event_id"])
            return {"status": "ok"}

        logger.error("Unrecognized invocation shape (no known detail-type): %s", safe_log_value(event))
        return {"status": "error", "message": "Unrecognized invocation"}

    return lambda_handler
