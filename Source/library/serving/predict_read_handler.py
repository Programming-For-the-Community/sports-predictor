"""
Shared predict-read Lambda logic. `make_multi_sport_lambda_handler` builds
the ONE shared predict-read Lambda's `lambda_handler`
(Source/aws-lambdas/shared/predict-read/handler.py), dispatching per
request to whichever sport's resource path was actually called -- API
Gateway integrates all 6 sports' `/{sport}/...` resource trees with this
same Lambda (Terraform/lambda-predict-read.tf), so the sport isn't known
until a request arrives.

_get_storage/_get_model_bucket/_get_predictions_table/_get_predict_invoker
stay DEFINED in the shared handler.py (not moved here) and are looked up in
`namespace` (that module's own globals()) BY KEY at call time rather than
captured as bound closure parameters -- the test suite patches them via
patch.object(shared_predict_read, "_get_model_bucket", ...) etc, which
only rebinds that name in the handler module's own __dict__ (== its
globals()). A closure that captured the original function object at
make_multi_sport_lambda_handler() call time would never see that
rebinding -- same pitfall/discipline documented in every other dedup pass
this codebase has done (normalize dispatch, predict dispatch).
_get_predict_invoker is sport-keyed (`_get_predict_invoker(sport)`) since
each sport's cache-miss trigger must invoke a DIFFERENT `predict` Lambda --
_get_storage/_get_model_bucket/_get_predictions_table take no sport
argument at all, since the underlying DynamoDB tables/S3 buckets are
already single, shared resources across every sport (confirmed via
Terraform/locals.tf) -- one set of singletons, reused across every sport's
requests in a warm container.

sport_configs' per-sport fn bundles (list_events_fn/get_season_
projection_fn/list_models_fn/freshness_inputs_fn/has_player_prop_route)
carry no such risk (never patched by attribute anywhere), so those stay
plain bound closures set up once per sport in the shared handler.py's own
SPORT_CONFIGS dict.

Two sport "shapes", both expressed via freshness_inputs_fn and
has_player_prop_route. freshness_inputs_fn's signature is (s3, get_storage,
event_id) -- get_storage is the CALLABLE (namespace["_get_storage"]), not
an already-resolved storage object, specifically so a sport whose
freshness check never needs storage never constructs that singleton on
this route at all, matching every sport's original per-request behavior
exactly:
  - Team sports (nfl/nba/ncaafb/ncaambb): freshness_inputs_fn ignores
    get_storage entirely and always returns (prediction_cache.
    current_core_model_versions(s3, sport), None) -- extra_fingerprint is
    always None, so prediction_cache.is_fresh's own default takes over,
    same as these sports' original 2-arg is_fresh(...) call. Also have a
    player-prop sub-route.
  - Individual sports (pga/f1): freshness_inputs_fn calls get_storage()
    to look up the event, since the right model-name set varies by the
    event's own event_type (pga_reads.model_versions_for/f1_reads.
    model_versions_for), and carries a real extra_fingerprint (pga_reads.
    rounds_fingerprint/f1_reads.result_fingerprint). No player-prop
    sub-route -- one compute already scores the whole field/grid, so
    there's nothing narrower to fetch.
"""
from library.aws import lambda_singletons
from library.schema.keys import event_key as build_event_key
from library.serving.viewer_analytics import log_viewer_analytics
from library.storage import prediction_cache

RETRY_AFTER_SECONDS = 5  # UI hint only, not enforced server-side


def _trigger_refresh(namespace, sport: str, s3, cache_key: str, async_payload: dict) -> None:
    if prediction_cache.claim_in_progress(s3, cache_key):
        namespace["_get_predict_invoker"](sport).invoke_async(async_payload)


def _serve_or_trigger(namespace, sport: str, response_fn, s3, cache_key: str, current_model_versions, extra_fingerprint, async_payload: dict) -> dict:
    entry = prediction_cache.get_cached(s3, cache_key)
    if entry is not None:
        if prediction_cache.is_error_entry(entry):
            if prediction_cache.is_error_entry_fresh(entry):
                status_code = prediction_cache.ERROR_STATUS_CODES.get(entry["error_type"], 500)
                return response_fn(status_code, {"error": entry["error"]})
            # expired -- fall through, retry as a miss
        else:
            if not prediction_cache.is_fresh(entry, current_model_versions, extra_fingerprint):
                # 203, not 200 -- lets the frontend show a "refreshing"
                # indicator and silently re-poll instead of treating this
                # the same as a genuinely current result.
                _trigger_refresh(namespace, sport, s3, cache_key, async_payload)
                return response_fn(203, {**entry["result"], "stale": True, "retry_after_seconds": RETRY_AFTER_SECONDS})
            return response_fn(200, {**entry["result"], "stale": False})

    _trigger_refresh(namespace, sport, s3, cache_key, async_payload)
    return response_fn(202, {"status": "computing", "retry_after_seconds": RETRY_AFTER_SECONDS})


def _handle_warmup(namespace, response_fn) -> dict:
    return response_fn(200, lambda_singletons.warm(
        namespace["_get_storage"], namespace["_get_model_bucket"], namespace["_get_predictions_table"],
    ))


def _handle_events(namespace, list_events_fn, response_fn, query_params: dict) -> dict:
    status = query_params.get("status", "scheduled")
    return response_fn(200, list_events_fn(namespace["_get_storage"](), status))


def _handle_models(namespace, list_models_fn, response_fn) -> dict:
    return response_fn(200, list_models_fn(namespace["_get_model_bucket"]()))


def _handle_season(namespace, get_season_projection_fn, response_fn) -> dict:
    body = get_season_projection_fn(namespace["_get_model_bucket"]())
    if body is None:
        return response_fn(503, {"error": "Season projection not yet available -- check back after the next scheduled update"})
    return response_fn(200, body)


def _handle_event_prediction(namespace, sport: str, freshness_inputs_fn, response_fn, event_id: str) -> dict:
    event_key_value = build_event_key(sport, event_id)
    s3 = namespace["_get_model_bucket"]()
    cache_key = prediction_cache.event_prediction_cache_key(sport, event_key_value)
    # get_storage passed as the CALLABLE, not called eagerly -- team
    # sports' own freshness_inputs_fn never needs storage at all (a fixed
    # CORE_EVENT_MODELS set, no per-event lookup), and calling _get_storage()
    # unconditionally here would construct a FeatureStorage singleton on
    # every event-prediction request for those sports, work the original
    # per-sport handlers never did on this route.
    current_versions, extra_fingerprint = freshness_inputs_fn(s3, namespace["_get_storage"], event_id)
    return _serve_or_trigger(
        namespace, sport, response_fn, s3, cache_key, current_versions, extra_fingerprint,
        {"detail-type": "ComputeAndCachePrediction", "route": "event", "event_id": event_id},
    )


def _handle_player_prop(namespace, sport: str, response_fn, event_id: str, entity_id: str, target_stat: str) -> dict:
    event_key_value = build_event_key(sport, event_id)
    s3 = namespace["_get_model_bucket"]()
    cache_key = prediction_cache.player_prop_cache_key(sport, event_key_value, entity_id, target_stat)
    current_version = prediction_cache.current_player_prop_model_version(s3, sport, target_stat)
    return _serve_or_trigger(
        namespace, sport, response_fn, s3, cache_key, current_version, None,
        {
            "detail-type": "ComputeAndCachePrediction", "route": "player_prop",
            "event_id": event_id, "entity_id": entity_id, "stat": target_stat,
        },
    )


def _build_routes(sport_configs: dict) -> dict:
    routes = {}
    for sport, config in sport_configs.items():
        routes[f"/{sport}/events"] = (sport, "events")
        routes[f"/{sport}/models"] = (sport, "models")
        routes[f"/{sport}/season"] = (sport, "season")
        routes[f"/{sport}/predictions/events/{{event_id}}"] = (sport, "event_prediction")
        if config["has_player_prop_route"]:
            routes[f"/{sport}/predictions/events/{{event_id}}/players/{{entity_id}}"] = (sport, "player_prop")
    return routes


def _dispatch_route(namespace, route: str, sport: str, config: dict, response_fn, path_params: dict, query_params: dict) -> dict:
    if route == "events":
        return _handle_events(namespace, config["list_events_fn"], response_fn, query_params)

    if route == "models":
        return _handle_models(namespace, config["list_models_fn"], response_fn)

    if route == "season":
        return _handle_season(namespace, config["get_season_projection_fn"], response_fn)

    if route == "event_prediction":
        return _handle_event_prediction(namespace, sport, config["freshness_inputs_fn"], response_fn, path_params["event_id"])

    # route == "player_prop" (only reachable for a sport whose config set
    # has_player_prop_route=True, per _build_routes above)
    target_stat = query_params.get("stat")
    if not target_stat:
        return response_fn(400, {"error": "Missing required query parameter: stat"})
    return _handle_player_prop(namespace, sport, response_fn, path_params["event_id"], path_params["entity_id"], target_stat)


def make_multi_sport_lambda_handler(*, sport_configs: dict, namespace, logger, response_fn):
    """sport_configs: {sport: {list_events_fn, get_season_projection_fn,
    list_models_fn, freshness_inputs_fn, has_player_prop_route}}, one entry
    per sport this Lambda serves. Builds the resource-path -> (sport,
    route) lookup once at Lambda cold start (not per-request) -- the same
    5 route shapes every sport already had (f"/{sport}/events" etc.), just
    resolved dynamically per request via this dict instead of baked into a
    per-sport closure the way the old per-sport predict-read Lambdas did."""
    routes = _build_routes(sport_configs)

    def lambda_handler(event, context):
        # EventBridge Scheduler warmup ping (scheduler-predict-read-
        # warmup.tf) -- no "resource" key, so this can't collide with a
        # real API Gateway route. One ping now warms every sport's shared
        # singletons at once (they're sport-agnostic), unlike the old
        # per-sport Lambdas' own per-sport pings.
        if event.get("warmup"):
            return _handle_warmup(namespace, response_fn)

        path_params = event.get("pathParameters") or {}
        query_params = event.get("queryStringParameters") or {}
        resource = event.get("resource", "")

        match = routes.get(resource)
        if match is None:
            return response_fn(404, {"error": f"No route for resource {resource!r}"})
        sport, route = match
        log_viewer_analytics(logger, sport, resource, event.get("httpMethod"), event.get("headers"))

        try:
            return _dispatch_route(namespace, route, sport, sport_configs[sport], response_fn, path_params, query_params)
        except Exception:
            logger.exception("Unhandled error serving %s", resource)
            return response_fn(500, {"error": "Internal server error"})

    return lambda_handler
