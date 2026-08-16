"""
Unit tests for nba/predict/event_prediction.py -- predict_event,
predict_player_prop, and predict_event_leaders. live_features/model_loader
are mocked at the module boundary; storage/s3/predictions_table are plain
MagicMocks.
"""
from unittest.mock import MagicMock, patch

import pytest

import event_prediction
import live_features
import model_loader


class TestModelNameToProp:
    def test_replaces_underscores_with_hyphens(self):
        assert event_prediction.model_name_to_prop("three_pointers_made") == "player-prop-three-pointers-made"


class TestNonNegative:
    def test_floors_negative_values_at_zero(self):
        assert event_prediction.non_negative(-5.0) == 0.0

    def test_leaves_positive_values_unchanged(self):
        assert event_prediction.non_negative(12.5) == 12.5


class TestReconcileScores:
    def test_preserves_margin_exactly(self):
        result = event_prediction.reconcile_scores(margin=7.0, home_score=112.0, away_score=105.0)
        assert result["home_score"] - result["away_score"] == pytest.approx(7.0)

    def test_preserves_combined_total(self):
        result = event_prediction.reconcile_scores(margin=3.0, home_score=110.0, away_score=107.0)
        assert result["home_score"] + result["away_score"] == pytest.approx(217.0)

    def test_floors_reconciled_scores_at_zero(self):
        result = event_prediction.reconcile_scores(margin=-40.0, home_score=3.0, away_score=3.0)
        assert result["home_score"] >= 0.0
        assert result["away_score"] >= 0.0


class TestPredictEvent:
    def test_predicts_win_probability_and_scores(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        with patch.object(event_prediction.live_features, "build_live_event_features", return_value={"f": 1}), \
             patch.object(event_prediction.model_loader, "load_current_model") as mock_load, \
             patch.object(event_prediction.model_loader, "predict") as mock_predict, \
             patch.object(event_prediction, "predict_event_leaders", return_value=None):
            mock_load.return_value = (MagicMock(), {"version": 1})
            mock_predict.side_effect = [0.65, 3.0, 112.0, 109.0]  # win_prob, margin, home_score, away_score

            result = event_prediction.predict_event(storage, s3, predictions_table, "401705127")

        assert result["predictions"]["win_probability"]["home_win_probability"] == 0.65
        assert result["predictions"]["margin"]["value"] == 3.0
        assert predictions_table.put_item.call_count == 4  # win_prob + margin + home_score + away_score

    def test_leaders_failure_does_not_fail_the_whole_prediction(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        with patch.object(event_prediction.live_features, "build_live_event_features", return_value={"f": 1}), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 1})), \
             patch.object(event_prediction.model_loader, "predict", return_value=1.0), \
             patch.object(event_prediction.live_features, "build_live_event_leader_candidates", side_effect=Exception("boom")):
            result = event_prediction.predict_event(storage, s3, predictions_table, "401705127")

        assert result["leaders"] is None


class TestPredictPlayerProp:
    def test_returns_a_non_negative_prediction(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        with patch.object(event_prediction.live_features, "build_live_player_features", return_value={"f": 1}), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 2})), \
             patch.object(event_prediction.model_loader, "predict", return_value=-3.0):
            result = event_prediction.predict_player_prop(storage, s3, predictions_table, "401705127", "101", "points")

        assert result["prediction"]["value"] == 0.0
        assert result["stat"] == "points"
        predictions_table.put_item.assert_called_once()

    def test_records_the_prediction_with_the_right_model_key(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        with patch.object(event_prediction.live_features, "build_live_player_features", return_value={"f": 1}), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 5})), \
             patch.object(event_prediction.model_loader, "predict", return_value=27.0):
            event_prediction.predict_player_prop(storage, s3, predictions_table, "401705127", "101", "points")

        written = predictions_table.put_item.call_args.args[0]
        assert written["model_key"] == "MODEL#player-prop-points#v5#PLAYER#101"


_EMPTY_TEAM_CANDIDATES = {"scoring": [], "rebounding": [], "assists": []}


class TestPredictEventLeaders:
    def test_returns_none_on_candidate_build_failure(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        with patch.object(event_prediction.live_features, "build_live_event_leader_candidates", side_effect=Exception("boom")):
            result = event_prediction.predict_event_leaders(storage, s3, predictions_table, "SPORT#NBA#EVENT#1")
        assert result is None

    def test_empty_categories_stay_empty_without_scoring(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        candidates = {"home": _EMPTY_TEAM_CANDIDATES, "away": _EMPTY_TEAM_CANDIDATES}
        with patch.object(event_prediction.live_features, "build_live_event_leader_candidates", return_value=candidates):
            result = event_prediction.predict_event_leaders(storage, s3, predictions_table, "SPORT#NBA#EVENT#1")

        assert result == {
            "home": {"scoring": [], "rebounding": [], "assists": []},
            "away": {"scoring": [], "rebounding": [], "assists": []},
        }
        predictions_table.put_item.assert_not_called()

    def test_scores_every_candidate_in_a_category(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        candidates = {
            "home": {**_EMPTY_TEAM_CANDIDATES, "scoring": [{"entity_id": "101"}, {"entity_id": "102"}]},
            "away": _EMPTY_TEAM_CANDIDATES,
        }
        storage.get_entity.return_value = {"name": "Jayson Tatum"}

        with patch.object(event_prediction.live_features, "build_live_event_leader_candidates", return_value=candidates), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 1})), \
             patch.object(event_prediction.model_loader, "predict", return_value=27.0):
            result = event_prediction.predict_event_leaders(storage, s3, predictions_table, "SPORT#NBA#EVENT#1")

        home_scoring = result["home"]["scoring"]
        assert [row["entity_id"] for row in home_scoring] == ["101", "102"]
        assert home_scoring[0]["name"] == "Jayson Tatum"
        assert home_scoring[0]["points"] == 27.0
        assert predictions_table.put_item.call_count == 2  # 2 candidates x 1 stat each (scoring only has 1)

    def test_skips_a_stat_whose_model_has_no_promoted_version(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        candidates = {
            "home": {**_EMPTY_TEAM_CANDIDATES, "scoring": [{"entity_id": "101"}]},
            "away": _EMPTY_TEAM_CANDIDATES,
        }
        storage.get_entity.return_value = None

        with patch.object(event_prediction.live_features, "build_live_event_leader_candidates", return_value=candidates), \
             patch.object(event_prediction.model_loader, "load_current_model", side_effect=model_loader.NoPromotedModelError("none")):
            result = event_prediction.predict_event_leaders(storage, s3, predictions_table, "SPORT#NBA#EVENT#1")

        home_scoring = result["home"]["scoring"][0]
        assert "points" not in home_scoring
        predictions_table.put_item.assert_not_called()


class TestComputeAndCacheEvent:
    """compute_and_cache_event -- the ComputeAndCachePrediction background
    worker predict-read/handler.py's own async invoke triggers on a
    prediction-cache miss/stale-refresh. predict_event itself is mocked
    out (already covered by TestPredictEvent above); this only checks the
    caching/error-mapping wrapper around it."""

    RESULT = {
        "event_key": "SPORT#NBA#EVENT#1",
        "predictions": {
            "win_probability": {"home_win_probability": 0.6, "model_version": 1},
            "margin": {"value": 3.0, "model_version": 2},
            "home_score": {"value": 112.0, "model_version": 3},
            "away_score": {"value": 109.0, "model_version": 4},
        },
        "leaders": None,
    }

    def test_success_writes_the_result_to_the_cache_and_clears_the_claim(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        storage.get_event.return_value = {"status": "completed"}
        with patch.object(event_prediction, "predict_event", return_value=self.RESULT), \
             patch.object(event_prediction.prediction_cache, "put_cached") as put_cached, \
             patch.object(event_prediction.prediction_cache, "clear_in_progress") as clear_in_progress:
            event_prediction.compute_and_cache_event(storage, s3, predictions_table, "1")

        cache_key, result, model_versions, event_status = put_cached.call_args.args[1:]
        assert result == self.RESULT
        assert model_versions == {"win_probability": 1, "margin": 2, "home_score": 3, "away_score": 4}
        assert event_status == "completed"
        clear_in_progress.assert_called_once()

    def test_event_not_found_writes_a_negative_cache_entry_instead_of_a_real_result(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        with patch.object(event_prediction, "predict_event", side_effect=live_features.EventNotFoundError("nope")), \
             patch.object(event_prediction.prediction_cache, "put_error_cached") as put_error_cached, \
             patch.object(event_prediction.prediction_cache, "put_cached") as put_cached, \
             patch.object(event_prediction.prediction_cache, "clear_in_progress") as clear_in_progress:
            event_prediction.compute_and_cache_event(storage, s3, predictions_table, "1")

        put_error_cached.assert_called_once()
        assert put_error_cached.call_args.args[2] == "EventNotFoundError"
        put_cached.assert_not_called()
        clear_in_progress.assert_called_once()

    def test_an_unrecognized_exception_still_clears_the_claim_before_propagating(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        with patch.object(event_prediction, "predict_event", side_effect=RuntimeError("boom")), \
             patch.object(event_prediction.prediction_cache, "clear_in_progress") as clear_in_progress, \
             pytest.raises(RuntimeError):
            event_prediction.compute_and_cache_event(storage, s3, predictions_table, "1")

        clear_in_progress.assert_called_once()


class TestComputeAndCachePlayerProp:
    def test_success_writes_the_result_to_the_cache_and_clears_the_claim(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        storage.get_event.return_value = {"status": "scheduled"}
        result = {"stat": "points", "prediction": {"value": 27.0, "model_version": 3}}
        with patch.object(event_prediction, "predict_player_prop", return_value=result), \
             patch.object(event_prediction.prediction_cache, "put_cached") as put_cached, \
             patch.object(event_prediction.prediction_cache, "clear_in_progress") as clear_in_progress:
            event_prediction.compute_and_cache_player_prop(storage, s3, predictions_table, "1", "101", "points")

        _, written_result, model_version, event_status = put_cached.call_args.args[1:]
        assert written_result == result
        assert model_version == 3
        assert event_status == "scheduled"
        clear_in_progress.assert_called_once()

    def test_no_promoted_model_writes_a_negative_cache_entry(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        with patch.object(event_prediction, "predict_player_prop", side_effect=model_loader.NoPromotedModelError("none")), \
             patch.object(event_prediction.prediction_cache, "put_error_cached") as put_error_cached, \
             patch.object(event_prediction.prediction_cache, "clear_in_progress") as clear_in_progress:
            event_prediction.compute_and_cache_player_prop(storage, s3, predictions_table, "1", "101", "points")

        put_error_cached.assert_called_once()
        assert put_error_cached.call_args.args[2] == "NoPromotedModelError"
        clear_in_progress.assert_called_once()
