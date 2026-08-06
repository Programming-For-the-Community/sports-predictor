"""
Unit tests for the NFL inference Lambda handler. live_features and
model_loader are the real modules (already covered by their own test
files, test_live_features.py/test_model_loader.py) -- FeatureStorage,
S3Manager, and the predictions DynamoDBTable are mocked here, since this
file's only job is verifying lambda_handler's own routing, response
shaping, and error-to-status-code mapping. event_prediction.py's and
season_projection.py's own request-shaping logic (the actual prediction
building) is covered here too, driven through lambda_handler exactly as
API Gateway/EventBridge would invoke it, rather than as separate unit
tests against those modules directly.

The nfl_predict module is registered in sys.modules by conftest.py.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

import event_prediction
import live_features
import model_loader
import nfl_predict
import season_projection
import season_simulation


@pytest.fixture(autouse=True)
def reset_singletons():
    """Clear the module-level singletons before and after each test, same
    reasoning as nfl_normalize's reset_storage fixture -- otherwise a mock
    installed by one test would leak into the next."""
    nfl_predict._storage = None
    nfl_predict._model_bucket = None
    nfl_predict._predictions_table = None
    yield
    nfl_predict._storage = None
    nfl_predict._model_bucket = None
    nfl_predict._predictions_table = None


def _api_event(resource: str, path_params: dict | None = None, query_params: dict | None = None) -> dict:
    return {
        "resource": resource,
        "pathParameters": path_params or {},
        "queryStringParameters": query_params or {},
    }


def _model_card(version: int) -> dict:
    return {"version": version, "feature_columns": []}


def _completed_event(event_key, season, home_id, away_id, home_score, away_score, *,
                      event_id=None, event_date="2025-09-14", season_type=2, week=1):
    return {
        "event_key": event_key, "event_id": event_id or event_key, "event_date": event_date,
        "season": season, "season_type": season_type, "week": week, "status": "completed",
        "participants": [
            {"entity_id": home_id, "role": "home", "result": {"score": home_score, "won": home_score > away_score}},
            {"entity_id": away_id, "role": "away", "result": {"score": away_score, "won": away_score > home_score}},
        ],
    }


def _scheduled_event(event_key, season, event_date, home_id, away_id, *,
                      event_id=None, season_type=2, week=1):
    return {
        "event_key": event_key, "event_id": event_id or event_key, "event_date": event_date,
        "season": season, "season_type": season_type, "week": week, "status": "scheduled",
        "participants": [
            {"entity_id": home_id, "role": "home", "result": None},
            {"entity_id": away_id, "role": "away", "result": None},
        ],
    }


class TestReconcileScores:
    def test_already_consistent_values_are_unchanged(self):
        result = event_prediction.reconcile_scores(margin=3.2, home_score=24.1, away_score=20.9)
        assert result == {"margin": 3.2, "home_score": pytest.approx(24.1), "away_score": pytest.approx(20.9)}

    def test_splits_the_discrepancy_evenly_and_preserves_the_total(self):
        result = event_prediction.reconcile_scores(margin=3.0, home_score=24.1, away_score=3.9)
        assert result["home_score"] - result["away_score"] == pytest.approx(3.0)
        assert result["home_score"] + result["away_score"] == pytest.approx(24.1 + 3.9)

    def test_never_produces_a_negative_score(self):
        result = event_prediction.reconcile_scores(margin=-40.0, home_score=10.0, away_score=12.0)
        assert result["home_score"] >= 0
        assert result["away_score"] >= 0


class TestEventOutcomeRoute:
    def test_returns_all_four_event_level_predictions_from_one_feature_row(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()

        with patch.object(live_features, "build_live_event_features", return_value={"home_elo": 1550}) as build, \
             patch.object(model_loader, "load_current_model", side_effect=[
                 (MagicMock(), _model_card(1)),
                 (MagicMock(), _model_card(2)),
                 (MagicMock(), _model_card(3)),
                 (MagicMock(), _model_card(4)),
             ]), \
             patch.object(model_loader, "predict", side_effect=[0.62, 3.2, 24.1, 20.9]):
            response = nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["event_key"] == "SPORT#NFL#EVENT#401547417"
        assert body["predictions"]["win_probability"] == {"home_win_probability": 0.62, "model_version": 1}
        assert body["predictions"]["margin"] == {"value": 3.2, "model_version": 2}
        assert body["predictions"]["home_score"] == {"value": 24.1, "model_version": 3}
        assert body["predictions"]["away_score"] == {"value": 20.9, "model_version": 4}
        build.assert_called_once_with(nfl_predict._storage, "nfl", "SPORT#NFL#EVENT#401547417")

    def test_reconciles_home_and_away_score_against_the_independent_margin_prediction(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()

        # margin says +3 (home favored by a field goal), but the
        # independently-trained home_score/away_score models disagree
        # wildly with that and with each other -- 24.1 vs 3.9 implies a
        # 20+ point margin, not 3.
        with patch.object(live_features, "build_live_event_features", return_value={}), \
             patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(1))), \
             patch.object(model_loader, "predict", side_effect=[0.62, 3.0, 24.1, 3.9]):
            response = nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        body = json.loads(response["body"])
        home = body["predictions"]["home_score"]["value"]
        away = body["predictions"]["away_score"]["value"]
        assert home - away == pytest.approx(3.0)  # now agrees with margin exactly
        assert home + away == pytest.approx(24.1 + 3.9)  # combined total is preserved

    def test_audits_one_predictions_table_write_per_model(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()

        with patch.object(live_features, "build_live_event_features", return_value={}), \
             patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(1))), \
             patch.object(model_loader, "predict", return_value=1.0):
            nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        assert nfl_predict._predictions_table.put_item.call_count == 4


class TestPlayerPropRoute:
    def test_returns_the_requested_stats_prediction(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()

        with patch.object(live_features, "build_live_player_features", return_value={"avg_passing_yards": 275}) as build, \
             patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(5))) as load, \
             patch.object(model_loader, "predict", return_value=267.4):
            response = nfl_predict.lambda_handler(
                _api_event(
                    "/nfl/predictions/events/{event_id}/players/{entity_id}",
                    {"event_id": "401547417", "entity_id": "mahomes-patrick"},
                    {"stat": "passing_yards"},
                ),
                None,
            )

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["entity_key"] == "SPORT#NFL#ENTITY#mahomes-patrick"
        assert body["stat"] == "passing_yards"
        assert body["prediction"] == {"value": 267.4, "model_version": 5}
        build.assert_called_once_with(
            nfl_predict._storage, "nfl", "SPORT#NFL#EVENT#401547417", "mahomes-patrick",
        )
        load.assert_called_once_with(nfl_predict._model_bucket, "nfl", "player-prop-passing-yards")
        nfl_predict._predictions_table.put_item.assert_called_once()

    def test_missing_stat_query_param_is_a_400_not_a_crash(self):
        response = nfl_predict.lambda_handler(
            _api_event(
                "/nfl/predictions/events/{event_id}/players/{entity_id}",
                {"event_id": "401547417", "entity_id": "mahomes-patrick"},
            ),
            None,
        )

        assert response["statusCode"] == 400


class TestRouting:
    def test_unknown_resource_is_a_404(self):
        response = nfl_predict.lambda_handler(_api_event("/nfl/something-else"), None)

        assert response["statusCode"] == 404

    def test_every_response_carries_cors_headers(self):
        response = nfl_predict.lambda_handler(_api_event("/nfl/something-else"), None)

        assert response["headers"]["Access-Control-Allow-Origin"] == "*"

    def test_events_and_models_are_not_served_here(self):
        # Moved to predict-read/handler.py (library.serving.nfl_reads,
        # covered by Source/tests/library/serving/test_nfl_reads.py and
        # Source/tests/aws-lambdas/nfl/test_predict_read.py) -- neither
        # route ever loads an ML model artifact, so they're served by a
        # separate, much lighter Lambda that never imports xgboost/
        # scikit-learn/pandas. API Gateway is repointed to send this
        # traffic there instead; this Lambda should never see it, but if
        # it somehow did, it must 404, not silently resurrect the route.
        assert nfl_predict.lambda_handler(_api_event("/nfl/events"), None)["statusCode"] == 404
        assert nfl_predict.lambda_handler(_api_event("/nfl/models"), None)["statusCode"] == 404

    def test_season_is_not_served_here(self):
        # Same reasoning, moved even later -- see TestScheduledSeasonProjection
        # above and predict/handler.py's own docstring. API Gateway is
        # repointed to nfl_predict_read (api-gateway-nfl-predict.tf); a
        # GET /nfl/season request should never reach this Lambda, but if
        # it somehow did, it must 404, not resurrect the old live-compute
        # route.
        assert nfl_predict.lambda_handler(_api_event("/nfl/season"), None)["statusCode"] == 404


class TestErrorMapping:
    def test_event_not_found_is_a_404(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()
        with patch.object(live_features, "build_live_event_features", side_effect=live_features.EventNotFoundError("nope")):
            response = nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "missing"}), None,
            )

        assert response["statusCode"] == 404

    def test_malformed_event_is_a_422(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()
        with patch.object(live_features, "build_live_event_features", side_effect=live_features.MalformedEventError("bad")):
            response = nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        assert response["statusCode"] == 422

    def test_no_promoted_model_is_a_503(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()
        with patch.object(live_features, "build_live_event_features", return_value={}), \
             patch.object(model_loader, "load_current_model", side_effect=model_loader.NoPromotedModelError("none yet")):
            response = nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        assert response["statusCode"] == 503

    def test_unexpected_exception_is_a_500_not_a_raw_502(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()
        with patch.object(live_features, "build_live_event_features", side_effect=RuntimeError("boom")):
            response = nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        assert response["statusCode"] == 500


def _candidate_row(entity_id: str) -> dict:
    return {"entity_id": entity_id, "home_elo": 1500}


class TestEventOutcomeRouteLeaders:
    def test_leaders_block_includes_player_name_when_the_entity_has_one(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()
        nfl_predict._storage.get_entity.return_value = {"entity_id": "qb1", "name": "Patrick Mahomes"}

        candidates = {
            "home": {"passing": [_candidate_row("qb1")], "receiving": [], "rushing": [], "sacks": []},
            "away": {"passing": [], "receiving": [], "rushing": [], "sacks": []},
        }

        with patch.object(live_features, "build_live_event_features", return_value={"home_elo": 1500}), \
             patch.object(live_features, "build_live_event_leader_candidates", return_value=candidates), \
             patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(1))), \
             patch.object(model_loader, "predict", return_value=267.0):
            response = nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        body = json.loads(response["body"])
        passing = body["leaders"]["home"]["passing"]
        assert passing["entity_id"] == "qb1"
        assert passing["name"] == "Patrick Mahomes"
        assert passing["passing_yards"] == 267.0

    def test_leader_predictions_are_recorded_same_as_a_manual_player_prop_query(self):
        # Same model_key shape _predict_player_prop already writes -- what
        # lets nfl_reads._leaders_comparison read either kind back later
        # for a completed event, see TestEventOutcomeRoute's own
        # audits-one-write-per-model test for the core-prediction
        # counterpart of this.
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()
        nfl_predict._storage.get_entity.return_value = {"entity_id": "qb1", "name": "Patrick Mahomes"}

        candidates = {
            "home": {"passing": [_candidate_row("qb1")], "receiving": [], "rushing": [], "sacks": []},
            "away": {"passing": [], "receiving": [], "rushing": [], "sacks": []},
        }

        with patch.object(live_features, "build_live_event_features", return_value={"home_elo": 1500}), \
             patch.object(live_features, "build_live_event_leader_candidates", return_value=candidates), \
             patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(3))), \
             patch.object(model_loader, "predict", return_value=267.0):
            nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        # 4 core predictions + 2 leader stats (passing_yards, passing_touchdowns
        # -- LEADER_CATEGORY_STATS["passing"]) for the one passing candidate.
        assert nfl_predict._predictions_table.put_item.call_count == 6
        leader_calls = [
            c for c in nfl_predict._predictions_table.put_item.call_args_list
            if "PLAYER#qb1" in c.args[0]["model_key"]
        ]
        assert len(leader_calls) == 2
        assert leader_calls[0].args[0]["model_key"] == "MODEL#player-prop-passing-yards#v3#PLAYER#qb1"
        assert leader_calls[0].args[0]["predicted_value"] == {"value": 267.0}

    def test_leader_prediction_recording_failure_does_not_break_the_leaders_block(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()
        nfl_predict._storage.get_entity.return_value = {"entity_id": "qb1", "name": "Patrick Mahomes"}
        # First 4 put_item calls (core predictions) succeed; the leader
        # writes are what should fail here without taking anything down.
        nfl_predict._predictions_table.put_item.side_effect = [None, None, None, None, Exception("DynamoDB down")]

        candidates = {
            "home": {"passing": [_candidate_row("qb1")], "receiving": [], "rushing": [], "sacks": []},
            "away": {"passing": [], "receiving": [], "rushing": [], "sacks": []},
        }

        with patch.object(live_features, "build_live_event_features", return_value={"home_elo": 1500}), \
             patch.object(live_features, "build_live_event_leader_candidates", return_value=candidates), \
             patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(3))), \
             patch.object(model_loader, "predict", return_value=267.0):
            response = nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        assert response["statusCode"] == 200
        passing = json.loads(response["body"])["leaders"]["home"]["passing"]
        assert passing["passing_yards"] == 267.0

    def test_leaders_is_none_when_candidate_building_fails_but_core_predictions_still_succeed(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()

        with patch.object(live_features, "build_live_event_features", return_value={"home_elo": 1500}), \
             patch.object(live_features, "build_live_event_leader_candidates", side_effect=RuntimeError("boom")), \
             patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(1))), \
             patch.object(model_loader, "predict", return_value=0.5):
            response = nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["leaders"] is None
        assert "win_probability" in body["predictions"]

    def test_a_missing_prop_model_for_one_stat_is_skipped_not_fatal(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()
        nfl_predict._storage.get_entity.return_value = None

        candidates = {
            "home": {"passing": [_candidate_row("qb1")], "receiving": [], "rushing": [], "sacks": []},
            "away": {"passing": [], "receiving": [], "rushing": [], "sacks": []},
        }

        with patch.object(live_features, "build_live_event_features", return_value={"home_elo": 1500}), \
             patch.object(live_features, "build_live_event_leader_candidates", return_value=candidates), \
             patch.object(model_loader, "load_current_model", side_effect=[
                 (MagicMock(), _model_card(1)),  # win-probability
                 (MagicMock(), _model_card(2)),  # margin
                 (MagicMock(), _model_card(3)),  # home-score
                 (MagicMock(), _model_card(4)),  # away-score
                 model_loader.NoPromotedModelError("no passing_yards model yet"),
                 (MagicMock(), _model_card(5)),  # passing_touchdowns
             ]), \
             patch.object(model_loader, "predict", side_effect=[0.62, 3.2, 24.1, 20.9, 2.0]):
            response = nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        assert response["statusCode"] == 200
        passing = json.loads(response["body"])["leaders"]["home"]["passing"]
        assert "passing_yards" not in passing
        assert passing["passing_touchdowns"] == 2.0

    def test_reuses_a_loaded_model_across_candidates_sharing_the_same_stat(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()
        nfl_predict._storage.get_entity.return_value = None

        candidates = {
            "home": {
                "passing": [], "rushing": [], "sacks": [],
                "receiving": [_candidate_row("wr1"), _candidate_row("wr2")],
            },
            "away": {"passing": [], "receiving": [], "rushing": [], "sacks": []},
        }
        load_call_count = {"n": 0}

        def fake_load(s3, sport, model_name):
            load_call_count["n"] += 1
            return (MagicMock(), _model_card(1))

        with patch.object(live_features, "build_live_event_features", return_value={"home_elo": 1500}), \
             patch.object(live_features, "build_live_event_leader_candidates", return_value=candidates), \
             patch.object(model_loader, "load_current_model", side_effect=fake_load), \
             patch.object(model_loader, "predict", return_value=50.0):
            nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        # 4 core models (win-probability, margin, home-score, away-score)
        # + 2 DISTINCT receiving models (yards, touchdowns) -- NOT 4,
        # even though there are 2 receiver candidates each needing both.
        assert load_call_count["n"] == 6


class TestSeasonStandingsInputs:
    def test_derives_wins_losses_and_point_differential_from_completed_events(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2025, "12", "24", 27, 20)],
            "scheduled": [],
        }[status]

        inputs = season_projection._season_standings_inputs(storage)

        assert inputs["wins"]["12"] == 1
        assert inputs["losses"]["24"] == 1
        assert inputs["point_differential"]["12"] == 7
        assert inputs["point_differential"]["24"] == -7

    def test_current_season_is_the_max_season_across_both_statuses(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2024, "12", "24", 27, 20)],
            "scheduled": [_scheduled_event("E2", 2025, "2025-09-21", "12", "7")],
        }[status]

        inputs = season_projection._season_standings_inputs(storage)

        assert inputs["current_season"] == 2025
        # The 2024 completed game shouldn't leak into this season's record.
        assert inputs["wins"] == {}

    def test_elo_carries_over_regressed_into_a_new_season_not_a_hard_reset(self):
        # A blowout in the prior season swings team "12"'s Elo rating well
        # above DEFAULT_STARTING_RATING -- confirm the new season's
        # current_ratings reflects a PARTIAL carryover (regressed toward
        # the default per compute_elo_ratings' season_carryover), not a
        # full reset to empty/default the way this used to work, and not
        # the full unregressed rating either.
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2024, "12", "24", 45, 3)],
            "scheduled": [_scheduled_event("E2", 2025, "2025-09-21", "12", "7")],
        }[status]

        inputs = season_projection._season_standings_inputs(storage)

        assert 1500 < inputs["current_ratings"]["12"] < 1531
        assert 1469 < inputs["current_ratings"]["24"] < 1500

    def test_remaining_games_and_team_next_event_reflect_chronological_order(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [],
            "scheduled": [
                _scheduled_event("E2", 2025, "2025-09-28", "12", "25"),
                _scheduled_event("E1", 2025, "2025-09-21", "12", "7"),
            ],
        }[status]

        inputs = season_projection._season_standings_inputs(storage)

        assert inputs["remaining_games"] == [("12", "7"), ("12", "25")]
        assert inputs["team_next_event"]["12"] == "E1"  # earliest game, not insertion order
        assert inputs["games_remaining"]["12"] == 2


class TestScheduledSeasonProjection:
    """GET /nfl/season no longer terminates on this Lambda at all -- moved
    to predict-read/handler.py (see TestRouting.test_season_is_not_served_here
    below and Source/tests/aws-lambdas/nfl/test_predict_read.py). This
    Lambda instead computes the projection on Terraform/scheduler-nfl-
    season-projection.tf's weekly direct EventBridge Scheduler invoke and
    writes it to S3 -- see predict/handler.py's own docstring for why."""

    def test_writes_the_season_projection_to_s3_under_the_expected_key(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2025, "12", "24", 27, 20)],
            "scheduled": [],
        }[status]
        nfl_predict._storage.get_all_player_game_stats.return_value = []

        simulated = {
            "12": {"projected_wins": 11.0, "division_winner_probability": 0.8, "playoff_probability": 0.9, "championship_probability": 0.2},
            "24": {"projected_wins": 6.0, "division_winner_probability": 0.1, "playoff_probability": 0.3, "championship_probability": 0.01},
        }
        with patch.object(season_simulation, "simulate_season", return_value=simulated):
            response = nfl_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        assert response == {"status": "ok"}
        nfl_predict._model_bucket.put_json.assert_called_once()
        key, body = nfl_predict._model_bucket.put_json.call_args[0]
        assert key == "season-projections/nfl/latest.json"
        assert body["season"] == 2025
        assert [row["team_id"] for row in body["standings"]] == ["12", "24"]
        assert body["standings"][0]["wins"] == 1

    def test_leaderboards_is_none_when_building_them_fails_but_standings_still_write(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2025, "12", "24", 27, 20)],
            "scheduled": [],
        }[status]
        nfl_predict._storage.get_all_player_game_stats.side_effect = RuntimeError("boom")

        with patch.object(season_simulation, "simulate_season", return_value={}):
            nfl_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        body = nfl_predict._model_bucket.put_json.call_args[0][1]
        assert body["leaderboards"] is None
        assert body["standings"] == []

    def test_leaderboards_include_player_names_and_are_capped_at_ten(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2025, "12", "24", 27, 20)],
            "scheduled": [_scheduled_event("E2", 2025, "2025-09-21", "12", "7")],
        }[status]
        nfl_predict._storage.get_all_player_game_stats.return_value = [
            {"entity_id": "qb1", "team_id": "12", "event_key": "E1", "stat_line": {"passing_yards": 300}},
        ]
        nfl_predict._storage.get_entity.return_value = {"entity_id": "qb1", "name": "Patrick Mahomes"}

        with patch.object(season_simulation, "simulate_season", return_value={}), \
             patch.object(live_features, "build_live_player_features", return_value={"entity_id": "qb1"}), \
             patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(1))), \
             patch.object(model_loader, "predict", return_value=280.0):
            nfl_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        body = nfl_predict._model_bucket.put_json.call_args[0][1]
        passing_leaders = body["leaderboards"]["passing_yards"]
        assert len(passing_leaders) <= 10
        assert passing_leaders[0]["name"] == "Patrick Mahomes"
        # current 300 + one remaining game projected at 280/game
        assert passing_leaders[0]["projected_total"] == pytest.approx(580.0)

    def test_leaderboards_include_season_wide_candidates_with_zero_recorded_stats(self):
        # The pre-season case: no completed games yet this season, so
        # current_totals_by_stat is empty for every stat -- candidates
        # must come entirely from each team's own next-event depth chart
        # (build_live_event_leader_candidates), not season_player_stats.
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [],
            "scheduled": [_scheduled_event("E2", 2026, "2026-09-10", "12", "7")],
        }[status]
        nfl_predict._storage.get_all_player_game_stats.return_value = []
        nfl_predict._storage.get_entity.return_value = {"entity_id": "qb1", "name": "Patrick Mahomes"}

        candidates = {
            "home": {"passing": [{"entity_id": "qb1", "team_id": "12"}], "receiving": [], "rushing": [], "sacks": []},
            "away": {"passing": [], "receiving": [], "rushing": [], "sacks": []},
        }

        with patch.object(season_simulation, "simulate_season", return_value={}), \
             patch.object(live_features, "build_live_event_leader_candidates", return_value=candidates), \
             patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(1))), \
             patch.object(model_loader, "predict", return_value=265.0):
            nfl_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        body = nfl_predict._model_bucket.put_json.call_args[0][1]
        passing_leaders = body["leaderboards"]["passing_yards"]
        assert len(passing_leaders) == 1
        assert passing_leaders[0]["entity_id"] == "qb1"
        assert passing_leaders[0]["current_total"] == 0.0
        # No recorded games -- projected total is purely the per-game
        # projection times this team's one remaining game.
        assert passing_leaders[0]["projected_total"] == pytest.approx(265.0)
