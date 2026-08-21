"""
Unit tests for ncaambb/predict-read/handler.py -- routing only; the actual
request-shaping logic (list_events/list_models) is exercised in
Source/tests/library/serving/test_ncaambb_reads.py, and the pure cache-
freshness rules in Source/tests/library/storage/test_prediction_cache.py.
This file's own TestPredictionRoutes exercises the real prediction_cache
functions end to end against a stateful fake S3 (see _s3_with_state)
rather than mocking them out, to catch a real wiring bug (wrong key,
wrong argument order) that a fully-mocked test wouldn't. The
ncaambb_predict_read module is registered in sys.modules by conftest.py.
"""
import json
import time
from unittest.mock import MagicMock, patch

import ncaambb_predict_read
from library.schema.keys import event_key as build_event_key
from library.storage.model_artifacts import current_version_key


def _api_event(resource, query_params=None):
    return {"resource": resource, "queryStringParameters": query_params}


def _predict_event(resource, path_params, query_params=None):
    return {"resource": resource, "pathParameters": path_params, "queryStringParameters": query_params}


def _s3_with_state(state: dict):
    """A MagicMock standing in for S3Manager, backed by a plain
    {key: json_value} dict -- object_exists/get_json read from it,
    put_json/delete_object are left as ordinary (unasserted-by-default)
    mock calls, same shape claim_in_progress/put_cached/put_error_cached
    actually call against a real S3Manager."""
    s3 = MagicMock()
    s3.object_exists.side_effect = lambda key: key in state
    s3.get_json.side_effect = lambda key: state[key]
    return s3


_CORE_MODEL_NAMES = {"win_probability": "win-probability", "margin": "score-margin", "home_score": "home-score", "away_score": "away-score"}


def _model_version_state(sport: str, versions: dict) -> dict:
    return {current_version_key(sport, _CORE_MODEL_NAMES[key]): {"version": version} for key, version in versions.items()}


class TestWarmup:
    def test_warmup_ping_touches_singletons_and_skips_routing(self):
        with patch.object(ncaambb_predict_read, "_get_storage") as mock_storage, \
             patch.object(ncaambb_predict_read, "_get_model_bucket") as mock_bucket, \
             patch.object(ncaambb_predict_read, "_get_predictions_table") as mock_table:
            response = ncaambb_predict_read.lambda_handler({"warmup": True}, None)

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"status": "warm"}
        mock_storage.assert_called_once()
        mock_bucket.assert_called_once()
        mock_table.assert_called_once()


class TestRouting:
    def test_events_route(self):
        with patch.object(ncaambb_predict_read, "_get_storage"), \
             patch.object(ncaambb_predict_read, "_get_predictions_table"), \
             patch.object(ncaambb_predict_read, "list_events", return_value={"sport": "ncaambb", "events": []}) as mock_list:
            response = ncaambb_predict_read.lambda_handler(_api_event("/ncaambb/events", {"status": "completed"}), None)

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"sport": "ncaambb", "events": []}
        assert mock_list.call_args.args[-1] == "completed"

    def test_events_route_defaults_status_to_scheduled(self):
        with patch.object(ncaambb_predict_read, "_get_storage"), \
             patch.object(ncaambb_predict_read, "_get_predictions_table"), \
             patch.object(ncaambb_predict_read, "list_events", return_value={}) as mock_list:
            ncaambb_predict_read.lambda_handler(_api_event("/ncaambb/events"), None)

        assert mock_list.call_args.args[-1] == "scheduled"

    def test_models_route(self):
        with patch.object(ncaambb_predict_read, "_get_model_bucket"), \
             patch.object(ncaambb_predict_read, "list_models", return_value={"sport": "ncaambb", "models": []}):
            response = ncaambb_predict_read.lambda_handler(_api_event("/ncaambb/models"), None)

        assert response["statusCode"] == 200

    def test_unknown_route_returns_404(self):
        response = ncaambb_predict_read.lambda_handler(_api_event("/ncaambb/unknown"), None)
        assert response["statusCode"] == 404

    def test_season_route_returns_the_cached_projection(self):
        ncaambb_predict_read._model_bucket = MagicMock()
        ncaambb_predict_read._model_bucket.object_exists.return_value = True
        ncaambb_predict_read._model_bucket.get_json.return_value = {"sport": "ncaambb", "season": 2026, "standings": []}

        response = ncaambb_predict_read.lambda_handler(_api_event("/ncaambb/season"), None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["season"] == 2026
        ncaambb_predict_read._model_bucket.get_json.assert_called_once_with("season-projections/ncaambb/latest.json")

    def test_season_route_returns_503_when_the_scheduled_job_hasnt_written_one_yet(self):
        # Currently always this state -- season_projection.py's own
        # scheduled write is step 8, not built yet.
        ncaambb_predict_read._model_bucket = MagicMock()
        ncaambb_predict_read._model_bucket.object_exists.return_value = False

        response = ncaambb_predict_read.lambda_handler(_api_event("/ncaambb/season"), None)

        assert response["statusCode"] == 503

    def test_unhandled_exception_returns_500(self):
        with patch.object(ncaambb_predict_read, "_get_storage"), \
             patch.object(ncaambb_predict_read, "_get_predictions_table"), \
             patch.object(ncaambb_predict_read, "list_events", side_effect=Exception("boom")):
            response = ncaambb_predict_read.lambda_handler(_api_event("/ncaambb/events"), None)

        assert response["statusCode"] == 500


class TestCorsHeaders:
    def test_response_includes_cors_headers(self):
        response = ncaambb_predict_read.lambda_handler(_api_event("/ncaambb/unknown"), None)
        assert response["headers"]["Access-Control-Allow-Origin"] == "*"


class TestPredictionRoutes:
    EVENT_ID = "401705127"
    EVENT_KEY = build_event_key("ncaambb", EVENT_ID)
    CACHE_KEY = f"predictions-cache/ncaambb/events/{EVENT_KEY}.json"
    EVENT_RESOURCE = "/ncaambb/predictions/events/{event_id}"

    def test_fresh_cache_hit_returns_the_cached_result_without_triggering_compute(self):
        versions = {"win_probability": 1, "margin": 1, "home_score": 1, "away_score": 1}
        state = _model_version_state("ncaambb", versions)
        state[self.CACHE_KEY] = {
            "model_versions": versions, "event_status": "completed",
            "cached_at_epoch": time.time(), "result": {"ok": True},
        }
        s3 = _s3_with_state(state)

        with patch.object(ncaambb_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(ncaambb_predict_read, "_get_predict_invoker") as get_invoker:
            response = ncaambb_predict_read.lambda_handler(
                _predict_event(self.EVENT_RESOURCE, {"event_id": self.EVENT_ID}), None,
            )

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"ok": True, "stale": False}
        get_invoker.assert_not_called()

    def test_stale_cache_hit_returns_203_with_the_cached_result_and_triggers_a_refresh(self):
        versions = {"win_probability": 1, "margin": 1, "home_score": 1, "away_score": 1}
        state = _model_version_state("ncaambb", versions)
        state[self.CACHE_KEY] = {
            "model_versions": versions, "event_status": "scheduled",
            "cached_at_epoch": time.time() - 999999, "result": {"ok": "stale"},
        }
        s3 = _s3_with_state(state)
        invoker = MagicMock()

        with patch.object(ncaambb_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(ncaambb_predict_read, "_get_predict_invoker", return_value=invoker):
            response = ncaambb_predict_read.lambda_handler(
                _predict_event(self.EVENT_RESOURCE, {"event_id": self.EVENT_ID}), None,
            )

        assert response["statusCode"] == 203
        body = json.loads(response["body"])
        assert body == {"ok": "stale", "stale": True, "retry_after_seconds": ncaambb_predict_read.RETRY_AFTER_SECONDS}
        invoker.invoke_async.assert_called_once_with(
            {"detail-type": "ComputeAndCachePrediction", "route": "event", "event_id": self.EVENT_ID},
        )

    def test_a_repromoted_model_makes_a_completed_events_own_cache_stale_too(self):
        cached_versions = {"win_probability": 1, "margin": 1, "home_score": 1, "away_score": 1}
        current = _model_version_state("ncaambb", {"win_probability": 2, "margin": 1, "home_score": 1, "away_score": 1})
        state = dict(current)
        state[self.CACHE_KEY] = {
            "model_versions": cached_versions, "event_status": "completed",
            "cached_at_epoch": time.time(), "result": {"ok": "old-model"},
        }
        s3 = _s3_with_state(state)
        invoker = MagicMock()

        with patch.object(ncaambb_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(ncaambb_predict_read, "_get_predict_invoker", return_value=invoker):
            response = ncaambb_predict_read.lambda_handler(
                _predict_event(self.EVENT_RESOURCE, {"event_id": self.EVENT_ID}), None,
            )

        assert response["statusCode"] == 203  # still serves the old value immediately, flagged stale
        invoker.invoke_async.assert_called_once()  # but kicks off a refresh

    def test_cache_miss_triggers_a_compute_and_returns_202(self):
        s3 = _s3_with_state({})
        invoker = MagicMock()

        with patch.object(ncaambb_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(ncaambb_predict_read, "_get_predict_invoker", return_value=invoker):
            response = ncaambb_predict_read.lambda_handler(
                _predict_event(self.EVENT_RESOURCE, {"event_id": self.EVENT_ID}), None,
            )

        assert response["statusCode"] == 202
        body = json.loads(response["body"])
        assert body["status"] == "computing"
        assert body["retry_after_seconds"] > 0
        invoker.invoke_async.assert_called_once_with(
            {"detail-type": "ComputeAndCachePrediction", "route": "event", "event_id": self.EVENT_ID},
        )

    def test_cache_miss_already_in_progress_does_not_trigger_a_second_compute(self):
        state = {f"{self.CACHE_KEY}.in-progress": {"started_at_epoch": time.time()}}
        s3 = _s3_with_state(state)
        invoker = MagicMock()

        with patch.object(ncaambb_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(ncaambb_predict_read, "_get_predict_invoker", return_value=invoker):
            response = ncaambb_predict_read.lambda_handler(
                _predict_event(self.EVENT_RESOURCE, {"event_id": self.EVENT_ID}), None,
            )

        assert response["statusCode"] == 202
        invoker.invoke_async.assert_not_called()

    def test_fresh_negative_cache_entry_returns_its_own_mapped_status_code(self):
        state = {self.CACHE_KEY: {"error_type": "EventNotFoundError", "error": "nope", "cached_at_epoch": time.time()}}
        s3 = _s3_with_state(state)

        with patch.object(ncaambb_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(ncaambb_predict_read, "_get_predict_invoker") as get_invoker:
            response = ncaambb_predict_read.lambda_handler(
                _predict_event(self.EVENT_RESOURCE, {"event_id": self.EVENT_ID}), None,
            )

        assert response["statusCode"] == 404
        assert json.loads(response["body"]) == {"error": "nope"}
        get_invoker.assert_not_called()

    def test_expired_negative_cache_entry_falls_through_to_a_fresh_attempt(self):
        state = {self.CACHE_KEY: {"error_type": "EventNotFoundError", "error": "nope", "cached_at_epoch": time.time() - 999999}}
        s3 = _s3_with_state(state)
        invoker = MagicMock()

        with patch.object(ncaambb_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(ncaambb_predict_read, "_get_predict_invoker", return_value=invoker):
            response = ncaambb_predict_read.lambda_handler(
                _predict_event(self.EVENT_RESOURCE, {"event_id": self.EVENT_ID}), None,
            )

        assert response["statusCode"] == 202
        invoker.invoke_async.assert_called_once()

    def test_player_prop_route_missing_stat_returns_400(self):
        response = ncaambb_predict_read.lambda_handler(
            _predict_event(
                "/ncaambb/predictions/events/{event_id}/players/{entity_id}",
                {"event_id": self.EVENT_ID, "entity_id": "101"}, {},
            ), None,
        )
        assert response["statusCode"] == 400

    def test_player_prop_route_fresh_cache_hit(self):
        stat = "points"
        cache_key = f"predictions-cache/ncaambb/events/{self.EVENT_KEY}/players/101/{stat}.json"
        state = {
            current_version_key("ncaambb", "player-prop-points"): {"version": 2},
            cache_key: {"model_versions": 2, "event_status": "completed", "cached_at_epoch": time.time(), "result": {"stat": stat}},
        }
        s3 = _s3_with_state(state)

        with patch.object(ncaambb_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(ncaambb_predict_read, "_get_predict_invoker") as get_invoker:
            response = ncaambb_predict_read.lambda_handler(
                _predict_event(
                    "/ncaambb/predictions/events/{event_id}/players/{entity_id}",
                    {"event_id": self.EVENT_ID, "entity_id": "101"}, {"stat": stat},
                ), None,
            )

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"stat": stat, "stale": False}
        get_invoker.assert_not_called()
