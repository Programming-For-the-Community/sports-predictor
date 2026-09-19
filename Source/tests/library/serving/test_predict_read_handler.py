"""
Unit tests for library.serving.predict_read_handler.
make_multi_sport_lambda_handler -- the shared dispatch/cache-serving body
the ONE shared predict-read Lambda (Source/aws-lambdas/shared/predict-read/
handler.py) wires up with a SPORT_CONFIGS dict, one entry per sport
(list_events_fn/get_season_projection_fn/list_models_fn/freshness_inputs_
fn/has_player_prop_route).

Exercised here against a fake sport config (fake namespace, fake list_
events_fn/etc, a plain response_fn) so this shared logic -- warmup,
resource-to-sport routing, events/models/season routing, 404/500 handling,
the full fresh/stale/miss/in-progress/negative-cache serve-or-trigger
contract, and the player-prop route's has_player_prop_route on/off switch
-- is tested exactly once instead of six near-identical copies per sport.
Source/tests/aws-lambdas/shared/test_predict_read.py is trimmed to just
each real sport's genuinely distinct pieces: confirming its own real
list_events/get_season_projection/list_models functions are wired in with
the right arguments (real wiring, not re-tested dispatch logic), pga/f1's
own freshness-fingerprint functions (real per-event-type model-version
lookup), and has_player_prop_route's own on/off value per sport.
"""
import json
from unittest.mock import MagicMock

import pytest

from library.serving.predict_read_handler import make_multi_sport_lambda_handler
from library.storage import prediction_cache

SPORT = "testsport"
OTHER_SPORT = "othersport"
EVENT_RESOURCE = f"/{SPORT}/predictions/events/{{event_id}}"
PLAYER_PROP_RESOURCE = f"/{SPORT}/predictions/events/{{event_id}}/players/{{entity_id}}"


def _response(status_code: int, body: dict) -> dict:
    return {"statusCode": status_code, "headers": {"Access-Control-Allow-Origin": "*"}, "body": json.dumps(body)}


class _FakeLogger:
    def exception(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass


@pytest.fixture
def namespace():
    return {
        "_get_storage": MagicMock(return_value=MagicMock(name="storage")),
        "_get_model_bucket": MagicMock(return_value=MagicMock(name="model_bucket")),
        "_get_predictions_table": MagicMock(return_value=MagicMock(name="predictions_table")),
        # Called as _get_predict_invoker(sport) now -- a plain MagicMock
        # returns the same .return_value regardless of the argument, so
        # every existing "namespace['_get_predict_invoker'].return_value...
        # " assertion below still holds unchanged.
        "_get_predict_invoker": MagicMock(return_value=MagicMock(name="predict_invoker")),
    }


def _sport_config(**overrides):
    config = {
        "list_events_fn": MagicMock(return_value={"events": []}),
        "get_season_projection_fn": MagicMock(return_value={"season": 2026}),
        "list_models_fn": MagicMock(return_value={"models": []}),
        "freshness_inputs_fn": MagicMock(return_value=({"win-probability": 1}, None)),
        "has_player_prop_route": True,
    }
    config.update(overrides)
    return config


def _build_handler(namespace, sport_configs=None, **config_overrides):
    if sport_configs is None:
        sport_configs = {SPORT: _sport_config(**config_overrides)}
    return make_multi_sport_lambda_handler(
        sport_configs=sport_configs, namespace=namespace, logger=_FakeLogger(), response_fn=_response,
    )


class TestWarmup:
    def test_warmup_ping_touches_singletons_and_skips_routing(self, namespace):
        handler = _build_handler(namespace)

        response = handler({"warmup": True}, None)

        assert response["statusCode"] == 200
        namespace["_get_storage"].assert_called_once()
        namespace["_get_model_bucket"].assert_called_once()
        namespace["_get_predictions_table"].assert_called_once()


class TestRouting:
    def test_events_route_defaults_status_to_scheduled(self, namespace):
        list_events_fn = MagicMock(return_value={"events": []})
        handler = _build_handler(namespace, list_events_fn=list_events_fn)

        response = handler({"resource": f"/{SPORT}/events"}, None)

        assert response["statusCode"] == 200
        list_events_fn.assert_called_once_with(namespace["_get_storage"].return_value, "scheduled")

    def test_events_route_passes_the_requested_status_through(self, namespace):
        list_events_fn = MagicMock(return_value={"events": []})
        handler = _build_handler(namespace, list_events_fn=list_events_fn)

        handler({"resource": f"/{SPORT}/events", "queryStringParameters": {"status": "completed"}}, None)

        list_events_fn.assert_called_once_with(namespace["_get_storage"].return_value, "completed")

    def test_models_route_returns_the_model_summary(self, namespace):
        handler = _build_handler(namespace, list_models_fn=MagicMock(return_value={"models": ["a"]}))

        response = handler({"resource": f"/{SPORT}/models"}, None)

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"models": ["a"]}

    def test_season_route_returns_the_cached_projection(self, namespace):
        handler = _build_handler(namespace, get_season_projection_fn=MagicMock(return_value={"season": 2026}))

        response = handler({"resource": f"/{SPORT}/season"}, None)

        assert response["statusCode"] == 200

    def test_season_route_returns_503_when_not_yet_available(self, namespace):
        handler = _build_handler(namespace, get_season_projection_fn=MagicMock(return_value=None))

        response = handler({"resource": f"/{SPORT}/season"}, None)

        assert response["statusCode"] == 503

    def test_unknown_subpath_for_a_known_sport_is_a_404(self, namespace):
        handler = _build_handler(namespace)

        response = handler({"resource": f"/{SPORT}/unknown"}, None)

        assert response["statusCode"] == 404

    def test_unknown_sport_is_a_404(self, namespace):
        handler = _build_handler(namespace)

        response = handler({"resource": f"/{OTHER_SPORT}/events"}, None)

        assert response["statusCode"] == 404

    def test_every_response_carries_the_headers_response_fn_sets(self, namespace):
        handler = _build_handler(namespace)

        response = handler({"resource": f"/{SPORT}/unknown"}, None)

        assert response["headers"]["Access-Control-Allow-Origin"] == "*"

    def test_unexpected_exception_is_a_500_not_a_raw_502(self, namespace):
        handler = _build_handler(namespace, list_events_fn=MagicMock(side_effect=RuntimeError("boom")))

        response = handler({"resource": f"/{SPORT}/events"}, None)

        assert response["statusCode"] == 500

    def test_two_sports_dispatch_to_their_own_distinct_functions(self, namespace):
        sport_a_events = MagicMock(return_value={"events": ["a"]})
        sport_b_events = MagicMock(return_value={"events": ["b"]})
        handler = _build_handler(
            namespace,
            sport_configs={
                SPORT: _sport_config(list_events_fn=sport_a_events),
                OTHER_SPORT: _sport_config(list_events_fn=sport_b_events),
            },
        )

        response_a = handler({"resource": f"/{SPORT}/events"}, None)
        response_b = handler({"resource": f"/{OTHER_SPORT}/events"}, None)

        assert json.loads(response_a["body"]) == {"events": ["a"]}
        assert json.loads(response_b["body"]) == {"events": ["b"]}
        sport_a_events.assert_called_once()
        sport_b_events.assert_called_once()


class TestPlayerPropRoute:
    def test_disabled_route_is_a_404_when_has_player_prop_route_is_false(self, namespace):
        handler = _build_handler(namespace, has_player_prop_route=False)

        response = handler(
            {
                "resource": PLAYER_PROP_RESOURCE,
                "pathParameters": {"event_id": "1", "entity_id": "2"},
                "queryStringParameters": {"stat": "passing_yards"},
            },
            None,
        )

        assert response["statusCode"] == 404

    def test_missing_stat_query_param_is_a_400(self, namespace):
        handler = _build_handler(namespace)

        response = handler(
            {"resource": PLAYER_PROP_RESOURCE, "pathParameters": {"event_id": "1", "entity_id": "2"}, "queryStringParameters": {}},
            None,
        )

        assert response["statusCode"] == 400

    def test_enabled_route_reaches_serve_or_trigger(self, namespace, monkeypatch):
        monkeypatch.setattr(prediction_cache, "get_cached", lambda s3, key: None)
        monkeypatch.setattr(prediction_cache, "claim_in_progress", lambda s3, key: True)
        handler = _build_handler(namespace)

        response = handler(
            {
                "resource": PLAYER_PROP_RESOURCE,
                "pathParameters": {"event_id": "1", "entity_id": "2"},
                "queryStringParameters": {"stat": "passing_yards"},
            },
            None,
        )

        assert response["statusCode"] == 202
        namespace["_get_predict_invoker"].return_value.invoke_async.assert_called_once()


class TestServeOrTrigger:
    """Exercises the shared fresh/stale/miss/in-progress/negative-cache
    contract once, via the event-prediction route every sport routes
    through identically (player-prop goes through the exact same
    _serve_or_trigger helper, covered separately above)."""

    EVENT = {"resource": EVENT_RESOURCE, "pathParameters": {"event_id": "1"}, "queryStringParameters": {}}

    def test_fresh_cache_hit_returns_200_without_triggering_compute(self, namespace, monkeypatch):
        monkeypatch.setattr(prediction_cache, "get_cached", lambda s3, key: {"result": {"ok": True}})
        monkeypatch.setattr(prediction_cache, "is_error_entry", lambda entry: False)
        monkeypatch.setattr(prediction_cache, "is_fresh", lambda entry, versions, fingerprint=None: True)
        handler = _build_handler(namespace)

        response = handler(self.EVENT, None)

        assert response["statusCode"] == 200
        assert json.loads(response["body"])["stale"] is False
        namespace["_get_predict_invoker"].assert_not_called()

    def test_stale_cache_hit_returns_203_and_triggers_a_refresh(self, namespace, monkeypatch):
        monkeypatch.setattr(prediction_cache, "get_cached", lambda s3, key: {"result": {"ok": True}})
        monkeypatch.setattr(prediction_cache, "is_error_entry", lambda entry: False)
        monkeypatch.setattr(prediction_cache, "is_fresh", lambda entry, versions, fingerprint=None: False)
        monkeypatch.setattr(prediction_cache, "claim_in_progress", lambda s3, key: True)
        handler = _build_handler(namespace)

        response = handler(self.EVENT, None)

        assert response["statusCode"] == 203
        body = json.loads(response["body"])
        assert body["stale"] is True
        namespace["_get_predict_invoker"].return_value.invoke_async.assert_called_once()

    def test_cache_miss_triggers_a_compute_and_returns_202(self, namespace, monkeypatch):
        monkeypatch.setattr(prediction_cache, "get_cached", lambda s3, key: None)
        monkeypatch.setattr(prediction_cache, "claim_in_progress", lambda s3, key: True)
        handler = _build_handler(namespace)

        response = handler(self.EVENT, None)

        assert response["statusCode"] == 202
        namespace["_get_predict_invoker"].return_value.invoke_async.assert_called_once()

    def test_cache_miss_already_in_progress_does_not_trigger_a_second_compute(self, namespace, monkeypatch):
        monkeypatch.setattr(prediction_cache, "get_cached", lambda s3, key: None)
        monkeypatch.setattr(prediction_cache, "claim_in_progress", lambda s3, key: False)
        handler = _build_handler(namespace)

        response = handler(self.EVENT, None)

        assert response["statusCode"] == 202
        namespace["_get_predict_invoker"].return_value.invoke_async.assert_not_called()

    def test_fresh_negative_cache_entry_returns_its_own_mapped_status_code(self, namespace, monkeypatch):
        monkeypatch.setattr(prediction_cache, "get_cached", lambda s3, key: {"error_type": "EventNotFoundError", "error": "nope"})
        monkeypatch.setattr(prediction_cache, "is_error_entry", lambda entry: True)
        monkeypatch.setattr(prediction_cache, "is_error_entry_fresh", lambda entry: True)
        handler = _build_handler(namespace)

        response = handler(self.EVENT, None)

        assert response["statusCode"] == prediction_cache.ERROR_STATUS_CODES["EventNotFoundError"]

    def test_expired_negative_cache_entry_falls_through_to_a_fresh_attempt(self, namespace, monkeypatch):
        monkeypatch.setattr(prediction_cache, "get_cached", lambda s3, key: {"error_type": "EventNotFoundError", "error": "nope"})
        monkeypatch.setattr(prediction_cache, "is_error_entry", lambda entry: True)
        monkeypatch.setattr(prediction_cache, "is_error_entry_fresh", lambda entry: False)
        monkeypatch.setattr(prediction_cache, "claim_in_progress", lambda s3, key: True)
        handler = _build_handler(namespace)

        response = handler(self.EVENT, None)

        assert response["statusCode"] == 202

    def test_freshness_inputs_fn_receives_the_storage_getter_lazily(self, namespace, monkeypatch):
        """freshness_inputs_fn gets namespace['_get_storage'] itself (the
        callable), not an already-resolved storage object -- a sport
        whose freshness check never needs storage (every team sport)
        should never construct that singleton on this route at all."""
        monkeypatch.setattr(prediction_cache, "get_cached", lambda s3, key: None)
        monkeypatch.setattr(prediction_cache, "claim_in_progress", lambda s3, key: True)
        freshness_inputs_fn = MagicMock(return_value=({}, None))
        handler = _build_handler(namespace, freshness_inputs_fn=freshness_inputs_fn)

        handler(self.EVENT, None)

        freshness_inputs_fn.assert_called_once_with(
            namespace["_get_model_bucket"].return_value, namespace["_get_storage"], "1",
        )
        namespace["_get_storage"].assert_not_called()

    def test_predict_invoker_is_resolved_for_the_request_s_own_sport(self, namespace, monkeypatch):
        """namespace['_get_predict_invoker'] is called with the sport the
        request actually routed to, not a bare zero-arg call -- the one
        genuinely per-sport singleton left in this shared Lambda."""
        monkeypatch.setattr(prediction_cache, "get_cached", lambda s3, key: None)
        monkeypatch.setattr(prediction_cache, "claim_in_progress", lambda s3, key: True)
        handler = _build_handler(namespace)

        handler(self.EVENT, None)

        namespace["_get_predict_invoker"].assert_called_once_with(SPORT)
