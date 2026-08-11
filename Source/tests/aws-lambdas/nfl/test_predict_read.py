"""
Unit tests for the NFL read-only serving Lambda. library.serving.nfl_reads
is the real module (already covered by its own test file,
Source/tests/library/serving/test_nfl_reads.py) -- FeatureStorage,
S3Manager, and the predictions DynamoDBTable are mocked here, since this
file's only job is verifying lambda_handler's own routing, response
shaping, and error handling.

The nfl_predict_read module is registered in sys.modules by conftest.py.
"""
import json
import time
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

import nfl_predict_read
from library.schema.keys import event_key as build_event_key
from library.storage.model_artifacts import current_version_key


@pytest.fixture(autouse=True)
def reset_singletons():
    nfl_predict_read._storage = None
    nfl_predict_read._model_bucket = None
    nfl_predict_read._predictions_table = None
    nfl_predict_read._predict_invoker = None
    yield
    nfl_predict_read._storage = None
    nfl_predict_read._model_bucket = None
    nfl_predict_read._predictions_table = None
    nfl_predict_read._predict_invoker = None


def _api_event(resource: str, query_params: dict | None = None) -> dict:
    return {"resource": resource, "queryStringParameters": query_params or {}}


def _predict_event(resource: str, path_params: dict, query_params: dict | None = None) -> dict:
    return {"resource": resource, "pathParameters": path_params, "queryStringParameters": query_params}


def _s3_with_state(state: dict):
    """A MagicMock standing in for S3Manager, backed by a plain
    {key: json_value} dict -- see test_predict_read_handler.py's own
    identical helper (NCAAFB) for the full reasoning."""
    s3 = MagicMock()
    s3.object_exists.side_effect = lambda key: key in state
    s3.get_json.side_effect = lambda key: state[key]
    return s3


_CORE_MODEL_NAMES = {"win_probability": "win-probability", "margin": "score-margin", "home_score": "home-score", "away_score": "away-score"}


def _model_version_state(sport: str, versions: dict) -> dict:
    return {current_version_key(sport, _CORE_MODEL_NAMES[key]): {"version": version} for key, version in versions.items()}


class TestEventsRoute:
    def test_returns_events_and_defaults_to_scheduled_status(self):
        nfl_predict_read._storage = MagicMock()
        nfl_predict_read._storage.get_all_events.return_value = []
        nfl_predict_read._predictions_table = MagicMock()

        response = nfl_predict_read.lambda_handler(_api_event("/nfl/events"), None)

        assert response["statusCode"] == 200
        nfl_predict_read._storage.get_all_events.assert_called_once_with("nfl", status="scheduled")

    def test_passes_the_requested_status_through(self):
        nfl_predict_read._storage = MagicMock()
        nfl_predict_read._storage.get_all_events.return_value = []
        nfl_predict_read._predictions_table = MagicMock()

        nfl_predict_read.lambda_handler(_api_event("/nfl/events", {"status": "completed"}), None)

        nfl_predict_read._storage.get_all_events.assert_called_once_with("nfl", status="completed")

    def test_response_body_matches_list_events_shape(self):
        # event_date is relative to today -- list_events' soonest-upcoming-week
        # scoping ignores anything more than a few days in the past (see
        # nfl_reads._STALE_SCHEDULED_GRACE_DAYS).
        event_date = (date.today() + timedelta(days=4)).isoformat()
        nfl_predict_read._storage = MagicMock()
        nfl_predict_read._storage.get_all_events.return_value = [
            {
                "event_id": "401547417", "event_date": event_date, "status": "scheduled",
                "season": 2025, "season_type": 2, "week": 4,
                "participants": [{"entity_id": "12", "role": "home"}, {"entity_id": "24", "role": "away"}],
            },
        ]
        nfl_predict_read._predictions_table = MagicMock()

        response = nfl_predict_read.lambda_handler(_api_event("/nfl/events"), None)

        body = json.loads(response["body"])
        assert body["sport"] == "nfl"
        assert body["events"][0]["event_id"] == "401547417"


class TestModelsRoute:
    def test_returns_a_model_card_summary_per_current_model(self):
        nfl_predict_read._model_bucket = MagicMock()
        nfl_predict_read._model_bucket.list_keys.return_value = ["nfl/win-probability/current.json"]
        nfl_predict_read._model_bucket.object_exists.return_value = True
        nfl_predict_read._model_bucket.get_json.side_effect = [
            {"version": 6},
            {
                "model_name": "win-probability", "algorithm": "xgboost", "version": 6,
                "trained_at": "2026-01-01T00:00:00Z", "accuracy": 0.63, "log_loss": 0.65,
                "feature_importances": {},
            },
        ]

        response = nfl_predict_read.lambda_handler(_api_event("/nfl/models"), None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["models"][0]["model_name"] == "win-probability"


class TestSeasonRoute:
    def test_returns_the_cached_projection(self):
        nfl_predict_read._model_bucket = MagicMock()
        nfl_predict_read._model_bucket.object_exists.return_value = True
        nfl_predict_read._model_bucket.get_json.return_value = {"sport": "nfl", "season": 2025, "standings": []}

        response = nfl_predict_read.lambda_handler(_api_event("/nfl/season"), None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["season"] == 2025
        nfl_predict_read._model_bucket.get_json.assert_called_once_with("season-projections/nfl/latest.json")

    def test_returns_503_when_the_scheduled_job_hasnt_written_one_yet(self):
        nfl_predict_read._model_bucket = MagicMock()
        nfl_predict_read._model_bucket.object_exists.return_value = False

        response = nfl_predict_read.lambda_handler(_api_event("/nfl/season"), None)

        assert response["statusCode"] == 503


class TestRouting:
    def test_unknown_resource_is_a_404(self):
        response = nfl_predict_read.lambda_handler(_api_event("/nfl/something-else"), None)

        assert response["statusCode"] == 404

    def test_every_response_carries_cors_headers(self):
        response = nfl_predict_read.lambda_handler(_api_event("/nfl/something-else"), None)

        assert response["headers"]["Access-Control-Allow-Origin"] == "*"

    def test_unexpected_exception_is_a_500_not_a_raw_502(self):
        nfl_predict_read._storage = MagicMock()
        nfl_predict_read._storage.get_all_events.side_effect = RuntimeError("boom")

        response = nfl_predict_read.lambda_handler(_api_event("/nfl/events"), None)

        assert response["statusCode"] == 500


class TestPredictionRoutes:
    """GET /nfl/predictions/events/{event_id} and .../players/{entity_id}
    -- moved here from predict/handler.py, now a read-through prediction
    cache (library.storage.prediction_cache) with async populate-on-miss.
    Exercises the real prediction_cache functions end to end against a
    stateful fake S3 (_s3_with_state) rather than mocking them out, same
    reasoning as test_predict_read_handler.py's own identical NCAAFB
    tests."""

    EVENT_ID = "401547417"
    EVENT_KEY = build_event_key("nfl", EVENT_ID)
    CACHE_KEY = f"predictions-cache/nfl/events/{EVENT_KEY}.json"
    EVENT_RESOURCE = "/nfl/predictions/events/{event_id}"

    def test_fresh_cache_hit_returns_the_cached_result_without_triggering_compute(self):
        versions = {"win_probability": 1, "margin": 1, "home_score": 1, "away_score": 1}
        state = _model_version_state("nfl", versions)
        state[self.CACHE_KEY] = {
            "model_versions": versions, "event_status": "completed",
            "cached_at_epoch": time.time(), "result": {"ok": True},
        }
        s3 = _s3_with_state(state)

        with patch.object(nfl_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(nfl_predict_read, "_get_predict_invoker") as get_invoker:
            response = nfl_predict_read.lambda_handler(
                _predict_event(self.EVENT_RESOURCE, {"event_id": self.EVENT_ID}), None,
            )

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"ok": True}
        get_invoker.assert_not_called()

    def test_stale_cache_hit_still_returns_the_cached_result_but_triggers_a_refresh(self):
        versions = {"win_probability": 1, "margin": 1, "home_score": 1, "away_score": 1}
        state = _model_version_state("nfl", versions)
        state[self.CACHE_KEY] = {
            "model_versions": versions, "event_status": "scheduled",
            "cached_at_epoch": time.time() - 999999, "result": {"ok": "stale"},
        }
        s3 = _s3_with_state(state)
        invoker = MagicMock()

        with patch.object(nfl_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(nfl_predict_read, "_get_predict_invoker", return_value=invoker):
            response = nfl_predict_read.lambda_handler(
                _predict_event(self.EVENT_RESOURCE, {"event_id": self.EVENT_ID}), None,
            )

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"ok": "stale"}
        invoker.invoke_async.assert_called_once_with(
            {"detail-type": "ComputeAndCachePrediction", "route": "event", "event_id": self.EVENT_ID},
        )

    def test_cache_miss_triggers_a_compute_and_returns_202(self):
        s3 = _s3_with_state({})
        invoker = MagicMock()

        with patch.object(nfl_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(nfl_predict_read, "_get_predict_invoker", return_value=invoker):
            response = nfl_predict_read.lambda_handler(
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

        with patch.object(nfl_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(nfl_predict_read, "_get_predict_invoker", return_value=invoker):
            response = nfl_predict_read.lambda_handler(
                _predict_event(self.EVENT_RESOURCE, {"event_id": self.EVENT_ID}), None,
            )

        assert response["statusCode"] == 202
        invoker.invoke_async.assert_not_called()

    def test_fresh_negative_cache_entry_returns_its_own_mapped_status_code(self):
        state = {self.CACHE_KEY: {"error_type": "EventNotFoundError", "error": "nope", "cached_at_epoch": time.time()}}
        s3 = _s3_with_state(state)

        with patch.object(nfl_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(nfl_predict_read, "_get_predict_invoker") as get_invoker:
            response = nfl_predict_read.lambda_handler(
                _predict_event(self.EVENT_RESOURCE, {"event_id": self.EVENT_ID}), None,
            )

        assert response["statusCode"] == 404
        assert json.loads(response["body"]) == {"error": "nope"}
        get_invoker.assert_not_called()

    def test_player_prop_route_missing_stat_returns_400(self):
        response = nfl_predict_read.lambda_handler(
            _predict_event(
                "/nfl/predictions/events/{event_id}/players/{entity_id}",
                {"event_id": self.EVENT_ID, "entity_id": "mahomes-patrick"}, {},
            ), None,
        )
        assert response["statusCode"] == 400

    def test_player_prop_route_fresh_cache_hit(self):
        stat = "passing_yards"
        cache_key = f"predictions-cache/nfl/events/{self.EVENT_KEY}/players/mahomes-patrick/{stat}.json"
        state = {
            current_version_key("nfl", "player-prop-passing-yards"): {"version": 2},
            cache_key: {"model_versions": 2, "event_status": "completed", "cached_at_epoch": time.time(), "result": {"stat": stat}},
        }
        s3 = _s3_with_state(state)

        with patch.object(nfl_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(nfl_predict_read, "_get_predict_invoker") as get_invoker:
            response = nfl_predict_read.lambda_handler(
                _predict_event(
                    "/nfl/predictions/events/{event_id}/players/{entity_id}",
                    {"event_id": self.EVENT_ID, "entity_id": "mahomes-patrick"}, {"stat": stat},
                ), None,
            )

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"stat": stat}
        get_invoker.assert_not_called()
