"""
Unit tests for the shared predict-read Lambda (Source/aws-lambdas/shared/
predict-read/handler.py), which replaced the 6 former per-sport predict-read
Lambdas. Confirms, per sport: real wiring into library.serving.
predict_read_handler.make_multi_sport_lambda_handler (which real
list_events/get_season_projection/list_models get called, with what
arguments, for THAT sport's own resource path), pga/f1's own freshness-
fingerprint functions, and one real end-to-end integration check per
prediction route (real prediction_cache + a stateful fake S3, not mocked
out) to catch a real wiring bug (wrong key, wrong argument order) a
fully-mocked test wouldn't. The shared dispatch/cache-serving CONTRACT
itself -- warmup, 404/CORS/500, and the full fresh/stale/miss/in-progress/
negative-cache decision tree -- is tested once, generically, in
Source/tests/library/serving/test_predict_read_handler.py. Each sport's
own list_events/list_models/get_season_projection request-shaping logic is
exercised in Source/tests/library/serving/test_<sport>_reads.py, and the
pure cache-freshness rules in Source/tests/library/storage/
test_prediction_cache.py.

The shared_predict_read module is registered in sys.modules by
Source/tests/aws-lambdas/shared/conftest.py.
"""
import json
import time
from unittest.mock import MagicMock, patch

import shared_predict_read
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


def _core_model_version_state(sport: str, versions: dict) -> dict:
    return {current_version_key(sport, _CORE_MODEL_NAMES[key]): {"version": version} for key, version in versions.items()}


def _model_version_state(sport: str, model_names: dict) -> dict:
    return {current_version_key(sport, name): {"version": 1} for name in model_names.values()}


class TestUnrecognizedResource:
    def test_unknown_sport_prefix_404s(self):
        response = shared_predict_read.lambda_handler(_api_event("/xfl/events"), None)
        assert response["statusCode"] == 404

    def test_known_sport_unknown_subpath_404s(self):
        response = shared_predict_read.lambda_handler(_api_event("/nfl/standings"), None)
        assert response["statusCode"] == 404


class _TeamSportRoutingMixin:
    """Shared body for nfl/nba/ncaafb/ncaambb's own TestRouting/
    TestPredictionRoutes classes -- each subclass sets SPORT, EVENT_ID, and
    the player-prop STAT it exercises."""

    SPORT: str
    EVENT_ID: str
    STAT: str

    @property
    def _reads_module(self):
        return getattr(shared_predict_read, f"{self.SPORT}_reads")

    def test_events_route_calls_the_real_list_events(self):
        with patch.object(shared_predict_read, "_get_storage"), \
             patch.object(shared_predict_read, "_get_predictions_table"), \
             patch.object(self._reads_module, "list_events", return_value={"sport": self.SPORT, "events": []}) as mock_list:
            response = shared_predict_read.lambda_handler(_api_event(f"/{self.SPORT}/events", {"status": "completed"}), None)

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"sport": self.SPORT, "events": []}
        assert mock_list.call_args.args[-1] == "completed"

    def test_events_route_defaults_status_to_scheduled(self):
        with patch.object(shared_predict_read, "_get_storage"), \
             patch.object(shared_predict_read, "_get_predictions_table"), \
             patch.object(self._reads_module, "list_events", return_value={}) as mock_list:
            shared_predict_read.lambda_handler(_api_event(f"/{self.SPORT}/events"), None)

        assert mock_list.call_args.args[-1] == "scheduled"

    def test_models_route_calls_the_real_list_models(self):
        with patch.object(shared_predict_read, "_get_model_bucket"), \
             patch.object(shared_predict_read, "list_models", return_value={"sport": self.SPORT, "models": []}):
            response = shared_predict_read.lambda_handler(_api_event(f"/{self.SPORT}/models"), None)

        assert response["statusCode"] == 200

    def test_season_route_calls_the_real_get_season_projection(self):
        with patch.object(shared_predict_read, "_get_model_bucket"), \
             patch.object(self._reads_module, "get_season_projection", return_value={"sport": self.SPORT, "season": 2026, "standings": []}):
            response = shared_predict_read.lambda_handler(_api_event(f"/{self.SPORT}/season"), None)

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"sport": self.SPORT, "season": 2026, "standings": []}

    def _event_key(self):
        return build_event_key(self.SPORT, self.EVENT_ID)

    def test_fresh_cache_hit_returns_the_cached_result(self):
        versions = {"win_probability": 1, "margin": 1, "home_score": 1, "away_score": 1}
        state = _core_model_version_state(self.SPORT, versions)
        cache_key = f"predictions-cache/{self.SPORT}/events/{self._event_key()}.json"
        state[cache_key] = {
            "model_versions": versions, "event_status": "completed",
            "cached_at_epoch": time.time(), "result": {"ok": True},
        }
        s3 = _s3_with_state(state)

        with patch.object(shared_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(shared_predict_read, "_get_predict_invoker") as get_invoker:
            response = shared_predict_read.lambda_handler(
                _predict_event(f"/{self.SPORT}/predictions/events/{{event_id}}", {"event_id": self.EVENT_ID}), None,
            )

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"ok": True, "stale": False}
        get_invoker.assert_not_called()

    def test_player_prop_route_missing_stat_returns_400(self):
        response = shared_predict_read.lambda_handler(
            _predict_event(
                f"/{self.SPORT}/predictions/events/{{event_id}}/players/{{entity_id}}",
                {"event_id": self.EVENT_ID, "entity_id": "101"}, {},
            ), None,
        )
        assert response["statusCode"] == 400

    def test_player_prop_route_fresh_cache_hit(self):
        cache_key = f"predictions-cache/{self.SPORT}/events/{self._event_key()}/players/101/{self.STAT}.json"
        state = {
            current_version_key(self.SPORT, f"player-prop-{self.STAT.replace('_', '-')}"): {"version": 2},
            cache_key: {"model_versions": 2, "event_status": "completed", "cached_at_epoch": time.time(), "result": {"stat": self.STAT}},
        }
        s3 = _s3_with_state(state)

        with patch.object(shared_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(shared_predict_read, "_get_predict_invoker") as get_invoker:
            response = shared_predict_read.lambda_handler(
                _predict_event(
                    f"/{self.SPORT}/predictions/events/{{event_id}}/players/{{entity_id}}",
                    {"event_id": self.EVENT_ID, "entity_id": "101"}, {"stat": self.STAT},
                ), None,
            )

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"stat": self.STAT, "stale": False}
        get_invoker.assert_not_called()


class TestNflRouting(_TeamSportRoutingMixin):
    SPORT = "nfl"
    EVENT_ID = "401671766"
    STAT = "passing_yards"


class TestNbaRouting(_TeamSportRoutingMixin):
    SPORT = "nba"
    EVENT_ID = "401705127"
    STAT = "points"


class TestNcaafbRouting(_TeamSportRoutingMixin):
    SPORT = "ncaafb"
    EVENT_ID = "401520281"
    STAT = "passing_yards"


class TestNcaambbRouting(_TeamSportRoutingMixin):
    SPORT = "ncaambb"
    EVENT_ID = "401705127"
    STAT = "points"


class TestPgaRouting:
    SPORT = "pga"
    EVENT_ID = "401811963"
    EVENT_RESOURCE = "/pga/predictions/events/{event_id}"

    def test_events_route_calls_the_real_list_events(self):
        with patch.object(shared_predict_read, "_get_storage"), \
             patch.object(shared_predict_read.pga_reads, "list_events", return_value={"sport": "pga", "events": []}) as mock_list:
            response = shared_predict_read.lambda_handler(_api_event("/pga/events", {"status": "completed"}), None)

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"sport": "pga", "events": []}
        assert mock_list.call_args.args[-1] == "completed"

    def test_events_route_defaults_status_to_scheduled(self):
        with patch.object(shared_predict_read, "_get_storage"), \
             patch.object(shared_predict_read.pga_reads, "list_events", return_value={}) as mock_list:
            shared_predict_read.lambda_handler(_api_event("/pga/events"), None)

        assert mock_list.call_args.args[-1] == "scheduled"

    def test_models_route_calls_the_real_list_models(self):
        with patch.object(shared_predict_read, "_get_model_bucket"), \
             patch.object(shared_predict_read, "list_models", return_value={"sport": "pga", "models": []}):
            response = shared_predict_read.lambda_handler(_api_event("/pga/models"), None)

        assert response["statusCode"] == 200

    def test_season_route_calls_the_real_get_season_projection(self):
        with patch.object(shared_predict_read, "_get_model_bucket"), \
             patch.object(shared_predict_read.pga_reads, "get_season_projection", return_value={"sport": "pga", "season": 2026, "standings": []}):
            response = shared_predict_read.lambda_handler(_api_event("/pga/season"), None)

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"sport": "pga", "season": 2026, "standings": []}

    def test_no_player_prop_route(self):
        response = shared_predict_read.lambda_handler(
            _predict_event("/pga/predictions/events/{event_id}/players/{entity_id}", {"event_id": self.EVENT_ID, "entity_id": "1"}, {"stat": "x"}), None,
        )
        assert response["statusCode"] == 404


class TestPgaFreshnessInputsForEvent:
    def test_returns_empty_dict_and_no_fingerprint_when_the_event_does_not_exist(self):
        storage = MagicMock()
        storage.get_event.return_value = None
        s3 = MagicMock()

        assert shared_predict_read._pga_freshness_inputs_for_event(s3, storage, "999") == ({}, None)

    def test_returns_empty_dict_for_an_unrecognized_event_type(self):
        storage = MagicMock()
        storage.get_event.return_value = {"event_type": "something_new"}
        s3 = MagicMock()

        assert shared_predict_read._pga_freshness_inputs_for_event(s3, storage, "999") == ({}, None)

    def test_field_event_resolves_the_full_field_model_map(self):
        storage = MagicMock()
        storage.get_event.return_value = {"event_type": "field"}
        s3 = _s3_with_state(_model_version_state("pga", shared_predict_read.pga_reads.FIELD_EVENT_MODEL_VERSIONS))

        versions, _ = shared_predict_read._pga_freshness_inputs_for_event(s3, storage, "999")

        assert versions["top_10_probability"] == 1

    def test_field_event_computes_a_real_rounds_fingerprint(self):
        storage = MagicMock()
        storage.get_event.return_value = {
            "event_type": "field",
            "participants": [{"entity_id": "1", "result": {"rounds": [{"round": 1}]}}],
        }
        s3 = _s3_with_state(_model_version_state("pga", shared_predict_read.pga_reads.FIELD_EVENT_MODEL_VERSIONS))

        _, fingerprint = shared_predict_read._pga_freshness_inputs_for_event(s3, storage, "999")

        assert fingerprint == 1

    def test_cup_event_has_no_fingerprint(self):
        storage = MagicMock()
        storage.get_event.return_value = {"event_type": "cup", "participants": []}
        s3 = _s3_with_state(_model_version_state("pga", shared_predict_read.pga_reads.CUP_MODEL_VERSIONS))

        _, fingerprint = shared_predict_read._pga_freshness_inputs_for_event(s3, storage, "999")

        assert fingerprint is None


class TestPgaPredictionRoute:
    """One real end-to-end check plus the one genuinely PGA-specific
    behavior (cup events using a different model map for freshness) -- see
    this file's own module docstring for why the rest of the fresh/stale/
    miss/negative-cache matrix isn't repeated here."""

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

    def test_fresh_cache_hit_returns_the_cached_result(self):
        versions = _model_version_state("pga", shared_predict_read.pga_reads.FIELD_EVENT_MODEL_VERSIONS)
        versions_flat = {key: 1 for key in shared_predict_read.pga_reads.FIELD_EVENT_MODEL_VERSIONS}
        state = dict(versions)
        state[self.CACHE_KEY] = {
            "model_versions": versions_flat, "event_status": "completed", "extra_fingerprint": 0,
            "cached_at_epoch": time.time(), "result": {"ok": True},
        }
        s3 = _s3_with_state(state)

        with patch.object(shared_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(shared_predict_read, "_get_storage", return_value=self._storage()), \
             patch.object(shared_predict_read, "_get_predict_invoker") as get_invoker:
            response = shared_predict_read.lambda_handler(
                _predict_event(self.EVENT_RESOURCE, {"event_id": self.EVENT_ID}), None,
            )

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"ok": True, "stale": False}
        get_invoker.assert_not_called()

    def test_cup_event_uses_the_cup_model_map_for_freshness(self):
        state = _model_version_state("pga", shared_predict_read.pga_reads.CUP_MODEL_VERSIONS)
        state[self.CACHE_KEY] = {
            "model_versions": {"cup_win_probability": 1}, "event_status": "scheduled",
            "cached_at_epoch": time.time(), "result": {"ok": "cup"},
        }
        s3 = _s3_with_state(state)

        with patch.object(shared_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(shared_predict_read, "_get_storage", return_value=self._storage(event_type="cup")), \
             patch.object(shared_predict_read, "_get_predict_invoker") as get_invoker:
            response = shared_predict_read.lambda_handler(
                _predict_event(self.EVENT_RESOURCE, {"event_id": self.EVENT_ID}), None,
            )

        assert response["statusCode"] == 200  # versions match -> fresh, not stale
        get_invoker.assert_not_called()


class TestF1Routing:
    def test_events_route_calls_the_real_list_events(self):
        with patch.object(shared_predict_read, "_get_storage"), \
             patch.object(shared_predict_read.f1_reads, "list_events", return_value={"sport": "f1", "events": []}) as mock_list:
            response = shared_predict_read.lambda_handler(_api_event("/f1/events", {"status": "completed"}), None)

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"sport": "f1", "events": []}
        assert mock_list.call_args.args[-1] == "completed"

    def test_events_route_defaults_status_to_scheduled(self):
        with patch.object(shared_predict_read, "_get_storage"), \
             patch.object(shared_predict_read.f1_reads, "list_events", return_value={}) as mock_list:
            shared_predict_read.lambda_handler(_api_event("/f1/events"), None)

        assert mock_list.call_args.args[-1] == "scheduled"

    def test_models_route_calls_the_real_list_models(self):
        with patch.object(shared_predict_read, "_get_model_bucket"), \
             patch.object(shared_predict_read, "list_models", return_value={"sport": "f1", "models": []}):
            response = shared_predict_read.lambda_handler(_api_event("/f1/models"), None)

        assert response["statusCode"] == 200

    def test_season_route_calls_the_real_get_season_projection(self):
        with patch.object(shared_predict_read, "_get_model_bucket"), \
             patch.object(shared_predict_read.f1_reads, "get_season_projection", return_value={"sport": "f1", "season": 2026, "driver_standings": []}):
            response = shared_predict_read.lambda_handler(_api_event("/f1/season"), None)

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"sport": "f1", "season": 2026, "driver_standings": []}


class TestF1FreshnessInputsForEvent:
    def test_returns_empty_dict_and_no_fingerprint_when_the_event_does_not_exist(self):
        storage = MagicMock()
        storage.get_event.return_value = None
        s3 = MagicMock()

        assert shared_predict_read._f1_freshness_inputs_for_event(s3, storage, "999") == ({}, None)

    def test_returns_empty_dict_for_an_unrecognized_event_type(self):
        storage = MagicMock()
        storage.get_event.return_value = {"event_type": "something_new"}
        s3 = MagicMock()

        assert shared_predict_read._f1_freshness_inputs_for_event(s3, storage, "999") == ({}, None)

    def test_field_event_resolves_the_full_field_model_map(self):
        storage = MagicMock()
        storage.get_event.return_value = {"event_type": "field", "participants": []}
        s3 = _s3_with_state(_model_version_state("f1", shared_predict_read.f1_reads.FIELD_EVENT_MODEL_VERSIONS))

        versions, _ = shared_predict_read._f1_freshness_inputs_for_event(s3, storage, "999")

        assert versions["win_probability"] == 1

    def test_field_event_computes_a_real_result_fingerprint(self):
        storage = MagicMock()
        storage.get_event.return_value = {
            "event_type": "field",
            "participants": [{"entity_id": "1", "result": {"status": "finished", "qualifying": {"position": 1}}}],
        }
        s3 = _s3_with_state(_model_version_state("f1", shared_predict_read.f1_reads.FIELD_EVENT_MODEL_VERSIONS))

        _, fingerprint = shared_predict_read._f1_freshness_inputs_for_event(s3, storage, "999")

        assert fingerprint == 2

    def test_sprint_event_uses_the_sprint_model_map(self):
        storage = MagicMock()
        storage.get_event.return_value = {"event_type": "sprint", "participants": []}
        s3 = _s3_with_state(_model_version_state("f1", shared_predict_read.f1_reads.SPRINT_EVENT_MODEL_VERSIONS))

        versions, _ = shared_predict_read._f1_freshness_inputs_for_event(s3, storage, "999")

        assert versions["win_probability"] == 1


class TestF1PredictionRoute:
    """One real end-to-end check plus the one genuinely F1-specific
    behavior (sprint events using a different model map for freshness) --
    see this file's own module docstring for why the rest of the fresh/
    stale/miss/negative-cache matrix isn't repeated here."""

    EVENT_ID = "2026-5"
    EVENT_KEY = build_event_key("f1", EVENT_ID)
    CACHE_KEY = f"predictions-cache/f1/events/{EVENT_KEY}.json"
    EVENT_RESOURCE = "/f1/predictions/events/{event_id}"

    def _storage(self, event_type="field"):
        storage = MagicMock()
        storage.get_event.return_value = {"event_type": event_type, "participants": []}
        return storage

    def test_fresh_cache_hit_returns_the_cached_result(self):
        versions = _model_version_state("f1", shared_predict_read.f1_reads.FIELD_EVENT_MODEL_VERSIONS)
        versions_flat = {key: 1 for key in shared_predict_read.f1_reads.FIELD_EVENT_MODEL_VERSIONS}
        state = dict(versions)
        state[self.CACHE_KEY] = {
            "model_versions": versions_flat, "event_status": "completed", "extra_fingerprint": 0,
            "cached_at_epoch": time.time(), "result": {"ok": True},
        }
        s3 = _s3_with_state(state)

        with patch.object(shared_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(shared_predict_read, "_get_storage", return_value=self._storage()), \
             patch.object(shared_predict_read, "_get_predict_invoker") as get_invoker:
            response = shared_predict_read.lambda_handler(
                _predict_event(self.EVENT_RESOURCE, {"event_id": self.EVENT_ID}), None,
            )

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"ok": True, "stale": False}
        get_invoker.assert_not_called()

    def test_sprint_event_uses_the_sprint_model_map_for_freshness(self):
        state = _model_version_state("f1", shared_predict_read.f1_reads.SPRINT_EVENT_MODEL_VERSIONS)
        state[self.CACHE_KEY] = {
            "model_versions": {key: 1 for key in shared_predict_read.f1_reads.SPRINT_EVENT_MODEL_VERSIONS},
            "event_status": "scheduled", "extra_fingerprint": 0,
            "cached_at_epoch": time.time(), "result": {"ok": "sprint"},
        }
        s3 = _s3_with_state(state)

        with patch.object(shared_predict_read, "_get_model_bucket", return_value=s3), \
             patch.object(shared_predict_read, "_get_storage", return_value=self._storage(event_type="sprint")), \
             patch.object(shared_predict_read, "_get_predict_invoker") as get_invoker:
            response = shared_predict_read.lambda_handler(
                _predict_event(self.EVENT_RESOURCE, {"event_id": self.EVENT_ID}), None,
            )

        assert response["statusCode"] == 200  # versions match -> fresh, not stale
        get_invoker.assert_not_called()
