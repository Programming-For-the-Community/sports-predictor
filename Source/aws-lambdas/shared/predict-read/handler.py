"""
Shared read-only serving Lambda for all 6 sports. Triggered by API Gateway
behind the Cognito authorizer -- every sport's own `/{sport}/events`,
`/{sport}/models`, `/{sport}/season`, `/{sport}/predictions/events/
{event_id}` (and, for team sports, `.../players/{entity_id}`) resource
integrates with this ONE Lambda (Terraform/lambda-predict-read.tf), not a
separate per-sport function -- see library.serving.predict_read_handler.
make_multi_sport_lambda_handler for the request-time sport dispatch.

Replaces the 6 former per-sport aws-lambdas/{sport}/predict-read/handler.py
files. Each sport's own routes/behavior are unchanged -- see library.
serving.predict_read_handler's own module docstring for the full fresh/
stale/miss/negative-cache contract every sport's prediction routes share,
and library.serving.{sport}_reads for that sport's own events/models/
season logic.

_get_storage/_get_model_bucket/_get_predictions_table need no sport
argument -- the entities/events/player_game_stats/team_game_stats/
predictions DynamoDB tables and the model-artifacts S3 bucket are already
single, shared resources across every sport (Terraform/locals.tf), so one
set of singletons serves every sport's requests in a warm container.
_get_predict_invoker(sport) is the one genuinely per-sport singleton --
each sport's cache-miss trigger must async-invoke a DIFFERENT `predict`
Lambda, resolved from a small per-sport env-var map.

No ML dependencies -- zip-packaged, not any predict Lambda's container
image.
"""
import json
import logging
import os

from library.aws import lambda_singletons
from library.aws.dynamodb_table import DynamoDBTable
from library.aws.lambda_invoker import LambdaInvoker
from library.aws.s3_manager import S3Manager
from library.schema.keys import event_key as build_event_key
from library.serving import f1_reads, nba_reads, ncaafb_reads, ncaambb_reads, nfl_reads, pga_reads
from library.serving.common import list_models
from library.serving.predict_read_handler import RETRY_AFTER_SECONDS, make_multi_sport_lambda_handler
from library.storage import prediction_cache
from library.storage.feature_storage import FeatureStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("predict-read")

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Content-Type": "application/json",
}

# Lazy singletons, shared across every sport, reused across warm invocations.
_storage: FeatureStorage | None = None
_model_bucket: S3Manager | None = None
_predictions_table: DynamoDBTable | None = None
_predict_invokers: dict[str, LambdaInvoker] = {}

# Each sport's own `predict` Lambda function name -- the one piece of
# config that's genuinely per-sport (Terraform/lambda-predict-read.tf).
_PREDICT_FUNCTION_NAME_ENV_VARS = {
    "nfl": "NFL_PREDICT_FUNCTION_NAME",
    "nba": "NBA_PREDICT_FUNCTION_NAME",
    "ncaafb": "NCAAFB_PREDICT_FUNCTION_NAME",
    "ncaambb": "NCAAMBB_PREDICT_FUNCTION_NAME",
    "pga": "PGA_PREDICT_FUNCTION_NAME",
    "f1": "F1_PREDICT_FUNCTION_NAME",
}


def _get_storage() -> FeatureStorage:
    return lambda_singletons.get_or_create(globals(), "_storage", FeatureStorage)


def _get_model_bucket() -> S3Manager:
    return lambda_singletons.get_or_create(
        globals(), "_model_bucket",
        lambda: S3Manager(os.environ["MODEL_ARTIFACTS_BUCKET_NAME"], region=os.environ.get("AWS_REGION")),
    )


def _get_predictions_table() -> DynamoDBTable:
    return lambda_singletons.get_or_create(
        globals(), "_predictions_table",
        lambda: DynamoDBTable(os.environ["PREDICTIONS_TABLE_NAME"], region=os.environ.get("AWS_REGION")),
    )


def _get_predict_invoker(sport: str) -> LambdaInvoker:
    if sport not in _predict_invokers:
        function_name = os.environ[_PREDICT_FUNCTION_NAME_ENV_VARS[sport]]
        _predict_invokers[sport] = LambdaInvoker(function_name, region=os.environ.get("AWS_REGION"))
    return _predict_invokers[sport]


def _response(status_code: int, body: dict) -> dict:
    return {"statusCode": status_code, "headers": _CORS_HEADERS, "body": json.dumps(body)}


def _team_sport_freshness_inputs(sport: str):
    # Team sports always compare against the one fixed CORE_EVENT_MODELS
    # set (no per-event-type variation, unlike pga/f1) -- extra_fingerprint
    # stays None, matching this sport's original 2-arg is_fresh(...) call.
    # get_storage (the singleton getter) is deliberately never called --
    # this route never needed FeatureStorage for these 4 sports.
    def _freshness_inputs(s3, get_storage, event_id: str):
        return prediction_cache.current_core_model_versions(s3, sport), None

    return _freshness_inputs


def _pga_freshness_inputs_for_event(s3, storage, event_id: str) -> tuple[dict, int | None]:
    """(current_model_versions, extra_fingerprint) -- the right model-name
    map depends on the event's own event_type (field/match_play/cup, see
    library.serving.pga_reads.model_versions_for). extra_fingerprint is
    pga_reads.rounds_fingerprint(event) -- None for match_play/cup (no
    per-round concept, is_fresh skips that check entirely) or once the
    event doesn't exist yet. current_model_versions falls back to {}
    (never matches a real cached entry's own model_versions, so is_fresh
    always reports stale rather than silently serving a wrong-shape
    comparison) if the event doesn't exist yet or has an event_type this
    Lambda doesn't recognize -- never raises on a read path."""
    event = storage.get_event(build_event_key("pga", event_id))
    if event is None:
        return {}, None
    try:
        models = pga_reads.model_versions_for(event.get("event_type"))
    except KeyError:
        return {}, None
    return prediction_cache.current_model_versions(s3, "pga", models), pga_reads.rounds_fingerprint(event)


def _f1_freshness_inputs_for_event(s3, storage, event_id: str) -> tuple[dict, int | None]:
    """Same shape as _pga_freshness_inputs_for_event -- the right model-name
    map depends on the event's own event_type (field/sprint, see
    library.serving.f1_reads.model_versions_for)."""
    event = storage.get_event(build_event_key("f1", event_id))
    if event is None:
        return {}, None
    try:
        models = f1_reads.model_versions_for(event.get("event_type"))
    except KeyError:
        return {}, None
    return prediction_cache.current_model_versions(s3, "f1", models), f1_reads.result_fingerprint(event)


SPORT_CONFIGS = {
    "nfl": {
        "list_events_fn": lambda storage, status: nfl_reads.list_events(storage, _get_predictions_table(), "nfl", status),
        "get_season_projection_fn": lambda model_bucket: nfl_reads.get_season_projection(model_bucket, "nfl"),
        "list_models_fn": lambda model_bucket: list_models(model_bucket, "nfl"),
        "freshness_inputs_fn": _team_sport_freshness_inputs("nfl"),
        "has_player_prop_route": True,
    },
    "nba": {
        "list_events_fn": lambda storage, status: nba_reads.list_events(storage, _get_predictions_table(), "nba", status),
        "get_season_projection_fn": lambda model_bucket: nba_reads.get_season_projection(model_bucket, "nba"),
        "list_models_fn": lambda model_bucket: list_models(model_bucket, "nba"),
        "freshness_inputs_fn": _team_sport_freshness_inputs("nba"),
        "has_player_prop_route": True,
    },
    "ncaafb": {
        "list_events_fn": lambda storage, status: ncaafb_reads.list_events(storage, _get_predictions_table(), "ncaafb", status),
        "get_season_projection_fn": lambda model_bucket: ncaafb_reads.get_season_projection(model_bucket, "ncaafb"),
        "list_models_fn": lambda model_bucket: list_models(model_bucket, "ncaafb"),
        "freshness_inputs_fn": _team_sport_freshness_inputs("ncaafb"),
        "has_player_prop_route": True,
    },
    "ncaambb": {
        "list_events_fn": lambda storage, status: ncaambb_reads.list_events(storage, _get_predictions_table(), "ncaambb", status),
        "get_season_projection_fn": lambda model_bucket: ncaambb_reads.get_season_projection(model_bucket, "ncaambb"),
        "list_models_fn": lambda model_bucket: list_models(model_bucket, "ncaambb"),
        "freshness_inputs_fn": _team_sport_freshness_inputs("ncaambb"),
        "has_player_prop_route": True,
    },
    "pga": {
        "list_events_fn": lambda storage, status: pga_reads.list_events(storage, "pga", status),
        "list_child_events_fn": lambda storage, parent_event_id: pga_reads.list_child_events(storage, "pga", parent_event_id),
        "get_season_projection_fn": lambda model_bucket: pga_reads.get_season_projection(model_bucket, "pga"),
        "list_models_fn": lambda model_bucket: list_models(model_bucket, "pga"),
        # Adapts the shared module's (s3, get_storage, event_id) calling
        # convention (get_storage as a lazy callable, for team sports that
        # never need it) onto this sport's own _pga_freshness_inputs_for_
        # event, which always needs storage and keeps its original
        # direct-storage signature since tests call it directly with an
        # already-built double.
        "freshness_inputs_fn": lambda s3, get_storage, event_id: _pga_freshness_inputs_for_event(s3, get_storage(), event_id),
        "has_player_prop_route": False,
    },
    "f1": {
        "list_events_fn": lambda storage, status: f1_reads.list_events(storage, "f1", status),
        "get_season_projection_fn": lambda model_bucket: f1_reads.get_season_projection(model_bucket, "f1"),
        "list_models_fn": lambda model_bucket: list_models(model_bucket, "f1"),
        "freshness_inputs_fn": lambda s3, get_storage, event_id: _f1_freshness_inputs_for_event(s3, get_storage(), event_id),
        "has_player_prop_route": False,
    },
}


lambda_handler = make_multi_sport_lambda_handler(
    sport_configs=SPORT_CONFIGS,
    namespace=globals(),
    logger=logger,
    response_fn=_response,
)
