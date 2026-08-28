"""
Unit tests for pga/predict-read/handler.py -- routing only; the actual
request-shaping logic (list_events) is exercised in Source/tests/library/
serving/test_pga_reads.py, and the pure cache-freshness rules in
Source/tests/library/storage/test_prediction_cache.py. TestPredictionRoute
exercises the real prediction_cache functions end to end against a
stateful fake S3 (see _s3_with_state) rather than mocking them out, to
catch a real wiring bug (wrong key, wrong argument order) that a fully-
mocked test wouldn't. The pga_predict_read module is registered in
sys.modules by conftest.py.
"""
import json
import time
from unittest.mock import MagicMock, patch

import pga_predict_read
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


def _model_version_state(sport: str, model_names: dict) -> dict:
    """model_names: {key: real_model_name}, all promoted at version 1."""
    return {current_version_key(sport, name): {"version": 1} for name in model_names.values()}


class TestWarmup:
    def test_warmup_ping_touches_singletons_and_skips_routing(self):
        with patch.object(pga_predict_read, "_get_storage") as mock_storage, \
             patch.object(pga_predict_read, "_get_model_bucket") as mock_bucket, \
             patch.object(pga_predict_read, "_get_predictions_table") as mock_table:
            response = pga_predict_read.lambda_handler({"warmup": True}, None)

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"status": "warm"}
        mock_storage.assert_called_once()
        mock_bucket.assert_called_once()
        mock_table.assert_called_once()


class TestRouting:
    def test_events_route(self):
        with patch.object(pga_predict_read, "_get_storage"), \
             patch.object(pga_predict_read.pga_reads, "list_events", return_value={"sport": "pga", "events": []}) as mock_list:
            response = pga_predict_read.lambda_handler(_api_event("/pga/events", {"status": "completed"}), None)

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"sport": "pga", "events": []}
        assert mock_list.call_args.args[-1] == "completed"

    def test_events_route_defaults_status_to_scheduled(self):
        with patch.object(pga_predict_read, "_get_storage"), \
             patch.object(pga_predict_read.pga_reads, "list_events", return_value={}) as mock_list:
            pga_predict_read.lambda_handler(_api_event("/pga/events"), None)

        assert mock_list.call_args.args[-1] == "scheduled"

    def test_models_route(self):
        with patch.object(pga_predict_read, "_get_model_bucket"), \
             patch.object(pga_predict_read, "list_models", return_value={"sport": "pga", "models": []}):
            response = pga_predict_read.lambda_handler(_api_event("/pga/models"), None)

        assert response["statusCode"] == 200

    def test_season_route_returns_the_cached_projection(self):
        with patch.object(pga_predict_read, "_get_model_bucket"), \
             patch.object(pga_predict_read.pga_reads, "get_season_projection", return_value={"sport": "pga", "season": 2026, "standings": []}):
            response = pga_predict_read.lambda_handler(_api_event("/pga/season"), None)

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"sport": "pga", "season": 2026, "standings": []}

    def test_season_route_returns_503_when_not_yet_available(self):
        with patch.object(pga_predict_read, "_get_model_bucket"), \
             patch.object(pga_predict_read.pga_reads, "get_season_projection", return_value=None):
            response = pga_predict_read.lambda_handler(_api_event("/pga/season"), None)

        assert response["statusCode"] == 503

    def test_unknown_route_returns_404(self):
        response = pga_predict_read.lambda_handler(_api_event("/pga/unknown"), None)
        assert response["statusCode"] == 404

    def test_unhandled_exception_returns_500(self):
        with patch.object(pga_predict_read, "_get_storage"), \
             patch.object(pga_predict_read.pga_reads, "list_events", side_effect=Exception("boom")):
            response = pga_predict_read.lambda_handler(_api_event("/pga/events"), None)

        assert response["statusCode"] == 500


class TestCorsHeaders:
    def test_response_includes_cors_headers(self):
        response = pga_predict_read.lambda_handler(_api_event("/pga/unknown"), None)
        assert response["headers"]["Access-Control-Allow-Origin"] == "*"


class TestFreshnessInputsForEvent:
    def test_returns_empty_dict_and_no_fingerprint_when_the_event_does_not_exist(self):
        storage = MagicMock()
        storage.get_event.return_value = None
        s3 = MagicMock()

        assert pga_predict_read._freshness_inputs_for_event(s3, storage, "999") == ({}, None)

    def test_returns_empty_dict_for_an_unrecognized_event_type(self):
        storage = MagicMock()
        storage.get_event.return_value = {"event_type": "something_new"}
        s3 = MagicMock()

        assert pga_predict_read._freshness_inputs_for_event(s3, storage, "999") == ({}, None)

    def test_field_event_resolves_the_full_field_model_map(self):
        storage = MagicMock()
        storage.get_event.return_value = {"event_type": "field"}
        s3 = _s3_with_state(_model_version_state("pga", pga_predict_read.pga_reads.FIELD_EVENT_MODEL_VERSIONS))

        versions, _ = pga_predict_read._freshness_inputs_for_event(s3, storage, "999")

        assert versions["top_10_probability"] == 1

    def test_field_event_computes_a_real_rounds_fingerprint(self):
        storage = MagicMock()
        storage.get_event.return_value = {
            "event_type": "field",
            "participants": [{"entity_id": "1", "result": {"rounds": [{"round": 1}]}}],
        }
        s3 = _s3_with_state(_model_version_state("pga", pga_predict_read.pga_reads.FIELD_EVENT_MODEL_VERSIONS))

        _, fingerprint = pga_predict_read._freshness_inputs_for_event(s3, storage, "999")

        assert fingerprint == 1

    def test_cup_event_has_no_fingerprint(self):
        storage = MagicMock()
        storage.get_event.return_value = {"event_type": "cup", "participants": []}
        s3 = _s3_with_state(_model_version_state("pga", pga_predict_read.pga_reads.CUP_MODEL_VERSIONS))

        _, fingerprint = pga_predict_read._freshness_inputs_for_event(s3, storage, "999")

        assert fingerprint is None


class TestPredictionRoute:
    EVENT_ID = "401811963"
    EVENT_KEY = build_event_key("pga", EVENT_ID)
    CACHE_KEY = f"predictions-cache/pga/events/{EVENT_KEY}.json"
    EVENT_RESOURCE = "/pga/predictions/events/{event_id}"

    def _storage(self, event_type="field"):
        storage = MagicMock()
        # participants=[] -> rounds_fingerprint is 0 for a "field" event
        # (not None), so cache-entry fixtures below must record a
        # matching extra_fingerprint to be treated as fresh.
        storage.get_event.return_value = {"event_type": event_type, "participants": []}
        return storage

    def test_fresh_cache_hit_returns_the_cached_result_without_triggering_compute(self):
        versions = _model_version_state("pga", pga_predict_read.pga_reads.FIELD_EVENT_MODEL_VERSIONS)
        versions_flat = {key: 1 for key in pga_predict_read.pga_reads.FIELD_EVENT_MODEL_VERSIONS}
        state = dict(versions)
        state[self.CACHE_KEY] = {
            "model_versions": versions_flat, "event_status": "completed", "extra_fingerprint": 0,
            "cached_at_epoch": time.time(), "result": {"ok": True},
        }
        s3 = _s3_with_state(state)

        with patch.object(pga_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(pga_predict_read, "_get_storage", return_value=self._storage()), \
             patch.object(pga_predict_read, "_get_predict_invoker") as get_invoker:
            response = pga_predict_read.lambda_handler(
                _predict_event(self.EVENT_RESOURCE, {"event_id": self.EVENT_ID}), None,
            )

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"ok": True, "stale": False}
        get_invoker.assert_not_called()

    def test_stale_cache_hit_returns_203_with_the_cached_result_and_triggers_a_refresh(self):
        state = {self.CACHE_KEY: {
            "model_versions": {"top_10_probability": 1}, "event_status": "scheduled",
            "cached_at_epoch": time.time() - 999999, "result": {"ok": "stale"},
        }}
        s3 = _s3_with_state(state)
        invoker = MagicMock()

        with patch.object(pga_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(pga_predict_read, "_get_storage", return_value=self._storage()), \
             patch.object(pga_predict_read, "_get_predict_invoker", return_value=invoker):
            response = pga_predict_read.lambda_handler(
                _predict_event(self.EVENT_RESOURCE, {"event_id": self.EVENT_ID}), None,
            )

        assert response["statusCode"] == 203
        body = json.loads(response["body"])
        assert body == {"ok": "stale", "stale": True, "retry_after_seconds": pga_predict_read.RETRY_AFTER_SECONDS}
        invoker.invoke_async.assert_called_once_with(
            {"detail-type": "ComputeAndCachePrediction", "route": "event", "event_id": self.EVENT_ID},
        )

    def test_cache_miss_triggers_a_compute_and_returns_202(self):
        s3 = _s3_with_state({})
        invoker = MagicMock()

        with patch.object(pga_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(pga_predict_read, "_get_storage", return_value=self._storage()), \
             patch.object(pga_predict_read, "_get_predict_invoker", return_value=invoker):
            response = pga_predict_read.lambda_handler(
                _predict_event(self.EVENT_RESOURCE, {"event_id": self.EVENT_ID}), None,
            )

        assert response["statusCode"] == 202
        body = json.loads(response["body"])
        assert body["status"] == "computing"
        invoker.invoke_async.assert_called_once_with(
            {"detail-type": "ComputeAndCachePrediction", "route": "event", "event_id": self.EVENT_ID},
        )

    def test_cache_miss_already_in_progress_does_not_trigger_a_second_compute(self):
        state = {f"{self.CACHE_KEY}.in-progress": {"started_at_epoch": time.time()}}
        s3 = _s3_with_state(state)
        invoker = MagicMock()

        with patch.object(pga_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(pga_predict_read, "_get_storage", return_value=self._storage()), \
             patch.object(pga_predict_read, "_get_predict_invoker", return_value=invoker):
            response = pga_predict_read.lambda_handler(
                _predict_event(self.EVENT_RESOURCE, {"event_id": self.EVENT_ID}), None,
            )

        assert response["statusCode"] == 202
        invoker.invoke_async.assert_not_called()

    def test_fresh_negative_cache_entry_returns_its_own_mapped_status_code(self):
        state = {self.CACHE_KEY: {"error_type": "EventNotFoundError", "error": "nope", "cached_at_epoch": time.time()}}
        s3 = _s3_with_state(state)

        with patch.object(pga_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(pga_predict_read, "_get_storage", return_value=self._storage()), \
             patch.object(pga_predict_read, "_get_predict_invoker") as get_invoker:
            response = pga_predict_read.lambda_handler(
                _predict_event(self.EVENT_RESOURCE, {"event_id": self.EVENT_ID}), None,
            )

        assert response["statusCode"] == 404
        assert json.loads(response["body"]) == {"error": "nope"}
        get_invoker.assert_not_called()

    def test_expired_negative_cache_entry_falls_through_to_a_fresh_attempt(self):
        state = {self.CACHE_KEY: {"error_type": "EventNotFoundError", "error": "nope", "cached_at_epoch": time.time() - 999999}}
        s3 = _s3_with_state(state)
        invoker = MagicMock()

        with patch.object(pga_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(pga_predict_read, "_get_storage", return_value=self._storage()), \
             patch.object(pga_predict_read, "_get_predict_invoker", return_value=invoker):
            response = pga_predict_read.lambda_handler(
                _predict_event(self.EVENT_RESOURCE, {"event_id": self.EVENT_ID}), None,
            )

        assert response["statusCode"] == 202
        invoker.invoke_async.assert_called_once()

    def test_cup_event_uses_the_cup_model_map_for_freshness(self):
        state = _model_version_state("pga", pga_predict_read.pga_reads.CUP_MODEL_VERSIONS)
        state[self.CACHE_KEY] = {
            "model_versions": {"cup_win_probability": 1}, "event_status": "scheduled",
            "cached_at_epoch": time.time(), "result": {"ok": "cup"},
        }
        s3 = _s3_with_state(state)

        with patch.object(pga_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(pga_predict_read, "_get_storage", return_value=self._storage(event_type="cup")), \
             patch.object(pga_predict_read, "_get_predict_invoker") as get_invoker:
            response = pga_predict_read.lambda_handler(
                _predict_event(self.EVENT_RESOURCE, {"event_id": self.EVENT_ID}), None,
            )

        assert response["statusCode"] == 200  # versions match -> fresh, not stale
        get_invoker.assert_not_called()
