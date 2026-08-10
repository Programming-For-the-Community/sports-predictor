"""
Unit tests for ncaafb/predict/event_prediction.py -- predict_event,
predict_player_prop, and predict_event_leaders. live_features/model_loader
are mocked at the module boundary; storage/s3/predictions_table are plain
MagicMocks.
"""
from unittest.mock import MagicMock, patch

import pytest

import event_prediction
import model_loader


class TestModelNameToProp:
    def test_replaces_underscores_with_hyphens(self):
        assert event_prediction.model_name_to_prop("passing_yards") == "player-prop-passing-yards"


class TestNonNegative:
    def test_floors_negative_values_at_zero(self):
        assert event_prediction.non_negative(-5.0) == 0.0

    def test_leaves_positive_values_unchanged(self):
        assert event_prediction.non_negative(12.5) == 12.5


class TestReconcileScores:
    def test_preserves_margin_exactly(self):
        result = event_prediction.reconcile_scores(margin=7.0, home_score=24.0, away_score=20.0)
        assert result["home_score"] - result["away_score"] == pytest.approx(7.0)

    def test_preserves_combined_total(self):
        result = event_prediction.reconcile_scores(margin=3.0, home_score=28.0, away_score=21.0)
        assert result["home_score"] + result["away_score"] == pytest.approx(49.0)

    def test_floors_reconciled_scores_at_zero(self):
        result = event_prediction.reconcile_scores(margin=-40.0, home_score=3.0, away_score=3.0)
        assert result["home_score"] >= 0.0
        assert result["away_score"] >= 0.0


class TestPredictEvent:
    def _mock_model(self, version=1, prediction=0.6):
        booster = MagicMock()
        card = {"version": version}
        return booster, card, prediction

    def test_predicts_win_probability_and_scores(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        with patch.object(event_prediction.live_features, "build_live_event_features", return_value={"f": 1}), \
             patch.object(event_prediction.model_loader, "load_current_model") as mock_load, \
             patch.object(event_prediction.model_loader, "predict") as mock_predict, \
             patch.object(event_prediction, "predict_event_leaders", return_value=None):
            mock_load.return_value = (MagicMock(), {"version": 1})
            mock_predict.side_effect = [0.65, 3.0, 24.0, 21.0]  # win_prob, margin, home_score, away_score

            result = event_prediction.predict_event(storage, s3, predictions_table, "401520281")

        assert result["predictions"]["win_probability"]["home_win_probability"] == 0.65
        assert result["predictions"]["margin"]["value"] == 3.0
        assert predictions_table.put_item.call_count == 4  # win_prob + margin + home_score + away_score

    def test_leaders_failure_does_not_fail_the_whole_prediction(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        with patch.object(event_prediction.live_features, "build_live_event_features", return_value={"f": 1}), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 1})), \
             patch.object(event_prediction.model_loader, "predict", return_value=1.0), \
             patch.object(event_prediction.live_features, "build_live_event_leaders", side_effect=Exception("boom")):
            result = event_prediction.predict_event(storage, s3, predictions_table, "401520281")

        assert result["leaders"] is None


class TestPredictPlayerProp:
    def test_returns_a_non_negative_prediction(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        with patch.object(event_prediction.live_features, "build_live_player_features", return_value={"f": 1}), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 2})), \
             patch.object(event_prediction.model_loader, "predict", return_value=-3.0):
            result = event_prediction.predict_player_prop(storage, s3, predictions_table, "401520281", "101", "passing_yards")

        assert result["prediction"]["value"] == 0.0
        assert result["stat"] == "passing_yards"
        predictions_table.put_item.assert_called_once()

    def test_records_the_prediction_with_the_right_model_key(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        with patch.object(event_prediction.live_features, "build_live_player_features", return_value={"f": 1}), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 5})), \
             patch.object(event_prediction.model_loader, "predict", return_value=250.0):
            event_prediction.predict_player_prop(storage, s3, predictions_table, "401520281", "101", "passing_yards")

        written = predictions_table.put_item.call_args.args[0]
        assert written["model_key"] == "MODEL#player-prop-passing-yards#v5#PLAYER#101"


class TestPredictEventLeaders:
    def test_returns_none_on_candidate_build_failure(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        with patch.object(event_prediction.live_features, "build_live_event_leaders", side_effect=Exception("boom")):
            result = event_prediction.predict_event_leaders(storage, s3, predictions_table, "SPORT#NCAAFB#EVENT#1")
        assert result is None

    def test_null_category_stays_null_without_scoring(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        candidates = {
            "home": {"passing": None, "receiving": None, "rushing": None},
            "away": {"passing": None, "receiving": None, "rushing": None},
        }
        with patch.object(event_prediction.live_features, "build_live_event_leaders", return_value=candidates):
            result = event_prediction.predict_event_leaders(storage, s3, predictions_table, "SPORT#NCAAFB#EVENT#1")

        assert result == candidates
        predictions_table.put_item.assert_not_called()

    def test_scores_a_found_candidate_for_both_stats_in_its_category(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        candidates = {
            "home": {"passing": {"entity_id": "101"}, "receiving": None, "rushing": None},
            "away": {"passing": None, "receiving": None, "rushing": None},
        }
        storage.get_entity.return_value = {"name": "Carson Beck"}

        with patch.object(event_prediction.live_features, "build_live_event_leaders", return_value=candidates), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 1})), \
             patch.object(event_prediction.model_loader, "predict", return_value=275.0):
            result = event_prediction.predict_event_leaders(storage, s3, predictions_table, "SPORT#NCAAFB#EVENT#1")

        home_passing = result["home"]["passing"]
        assert home_passing["entity_id"] == "101"
        assert home_passing["name"] == "Carson Beck"
        assert home_passing["passing_yards"] == 275.0
        assert home_passing["passing_touchdowns"] == 275.0  # same mocked predict() for both stats
        assert predictions_table.put_item.call_count == 2

    def test_skips_a_stat_whose_model_has_no_promoted_version(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        candidates = {
            "home": {"passing": {"entity_id": "101"}, "receiving": None, "rushing": None},
            "away": {"passing": None, "receiving": None, "rushing": None},
        }
        storage.get_entity.return_value = None

        with patch.object(event_prediction.live_features, "build_live_event_leaders", return_value=candidates), \
             patch.object(event_prediction.model_loader, "load_current_model", side_effect=model_loader.NoPromotedModelError("none")):
            result = event_prediction.predict_event_leaders(storage, s3, predictions_table, "SPORT#NCAAFB#EVENT#1")

        home_passing = result["home"]["passing"]
        assert "passing_yards" not in home_passing
        assert "passing_touchdowns" not in home_passing
        predictions_table.put_item.assert_not_called()
