from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from library.ml import backtest, target_baseline, train_score_model_common
from library.serving import model_loader


def _rated(n=60):
    gap = np.linspace(-300, 300, n)
    X = pd.DataFrame({"home_elo": 1500 + gap, "away_elo": np.full(n, 1500.0)})
    return X, pd.Series(gap / 25 + 2.5)


class TestFit:
    def test_recovers_points_per_elo_and_home_edge(self):
        baseline = target_baseline.fit_elo_linear(*_rated())

        assert baseline["kind"] == "elo_linear"
        assert baseline["slope"] == pytest.approx(1 / 25)
        assert baseline["intercept"] == pytest.approx(2.5)

    def test_no_elo_columns_or_too_few_rated_rows_gives_no_baseline(self):
        X, y = _rated()

        assert target_baseline.fit_elo_linear(X.drop(columns="away_elo"), y) is None
        assert target_baseline.fit_elo_linear(X.head(10), y.head(10)) is None


class TestApply:
    def test_an_unrated_row_gets_the_intercept_and_none_gives_zeros(self):
        X = pd.DataFrame({"home_elo": [1600.0, np.nan], "away_elo": [1500.0, 1500.0]})
        baseline = {"kind": "elo_linear", "slope": 0.04, "intercept": 2.0}

        assert list(target_baseline.apply(baseline, X)) == pytest.approx([6.0, 2.0])
        assert list(target_baseline.apply(None, X)) == [0.0, 0.0]


class _Residual:
    algorithm = "fake"
    artifact_filename = "model.fake"

    def __init__(self):
        self.trained_on = None

    def tune_and_fit(self, X, y):
        self.trained_on = y
        return "estimator", {}

    def predict(self, estimator, X):
        return np.zeros(len(X))

    def feature_importances(self, estimator, columns):
        return {}

    def serialize(self, estimator):
        return b""


class TestBacktestWithBaseline:
    def test_trains_on_the_correction_and_scores_on_the_full_margin(self):
        X, y = _rated()
        baseline = target_baseline.fit_elo_linear(X, y)
        adapter = _Residual()
        saved = []

        with patch.object(backtest, "_current_champion", return_value=None), \
             patch.object(backtest.training_common, "load_run_progress", return_value=None), \
             patch.object(backtest.training_common, "save_run_progress"), \
             patch.object(backtest.training_common, "clear_run_progress"), \
             patch.object(backtest.training_common, "set_current_version"), \
             patch.object(backtest.training_common, "save_model_artifact", side_effect=lambda *a: saved.append(a[6]) or {"version": 1, **a[6]}):
            backtest.run_backtest(
                MagicMock(), "ncaafb", "score-margin", "regression", backtest.HoldoutSplit(X, y, X, y), [adapter], {},
                {"train_rows": 60, "test_rows": 60}, ["rmse"], "rmse", "run-1", backtest.RunOptions(target_baseline=baseline),
            )

        assert np.allclose(adapter.trained_on, 0.0)
        assert saved[0]["rmse"] == pytest.approx(0.0, abs=1e-9)
        assert saved[0]["target_baseline"] == baseline


class TestServingBaseline:
    def test_card_predictions_add_the_baseline_back(self):
        adapter = MagicMock()
        adapter.predict.return_value = np.array([1.0])
        card = {"algorithm": "fake", "target_baseline": {"kind": "elo_linear", "slope": 0.04, "intercept": 2.0}}
        X = pd.DataFrame({"home_elo": [1600.0], "away_elo": [1500.0]})

        with patch.dict(model_loader.ADAPTERS, {"fake": adapter}):
            assert model_loader.card_predictions("estimator", card, X)[0] == pytest.approx(7.0)


class TestScoreTargets:
    @pytest.mark.parametrize("score_target, has_baseline", [("margin", True), ("home_score", False)])
    def test_only_the_margin_learns_a_correction_to_elo(self, score_target, has_baseline):
        n = 80
        df = pd.DataFrame({
            "event_key": [f"E{i}" for i in range(n)], "event_date": pd.date_range("2024-09-01", periods=n).astype(str),
            "home_entity_id": "A", "away_entity_id": "B",
            "home_elo": np.linspace(1400, 1600, n), "away_elo": 1500.0,
            "home_avg_points_scored": 20.0, "home_avg_points_allowed": 20.0,
            "away_avg_points_scored": 20.0, "away_avg_points_allowed": 20.0,
            "label_home_score": np.arange(n) % 30 + 10, "label_away_score": 20,
        })

        with patch.object(train_score_model_common.backtest, "run_backtest") as run:
            train_score_model_common.train(
                MagicMock(), df, score_target, sport="ncaafb", candidates=[], logger=MagicMock(),
                non_feature_columns={"event_key", "event_date", "home_entity_id", "away_entity_id"},
            )

        assert (run.call_args.kwargs["options"].target_baseline is not None) is has_baseline
        assert len(run.call_args.kwargs["options"].sample_weights) == len(run.call_args.kwargs["split"].X_train)
