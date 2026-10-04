from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from library.ml import backtest, training_common


class TestRecencyWeights:
    def test_each_year_back_counts_less(self):
        weights = training_common.recency_weights(pd.Series(["2026-09-01", "2025-09-01", "2024-09-01"]))

        assert weights[0] == 1.0
        assert weights[1] == pytest.approx(training_common.RECENCY_WEIGHT_PER_YEAR, rel=1e-2)
        assert weights[2] == pytest.approx(training_common.RECENCY_WEIGHT_PER_YEAR ** 2, rel=1e-2)


class _Adapter:
    algorithm = "fake"

    def __init__(self, weighted_predictions):
        self._weighted = weighted_predictions
        self.weights = None

    def fit(self, X, y, params, sample_weight=None):
        self.weights = sample_weight
        return "weighted-estimator"

    def predict(self, estimator, X):
        return self._weighted


def _split():
    X = pd.DataFrame({"a": [1.0, 2.0, 3.0]})
    y = pd.Series([1.0, 2.0, 3.0])
    return backtest.HoldoutSplit(X, y, X, y)


def _weighted(adapter, metrics):
    return backtest._recency_weighted(
        adapter, {}, _split(), np.zeros(3), np.array([0.5, 0.7, 1.0]), "regression", "rmse",
        ("estimator", np.zeros(3), metrics),
    )


class TestRecencyWeightedRefit:
    def test_a_better_weighted_refit_replaces_the_candidate(self):
        adapter = _Adapter(np.array([1.0, 2.0, 3.0]))

        estimator, predictions, metrics, weighted = _weighted(adapter, {"rmse": 2.0})

        assert (estimator, weighted, metrics["rmse"]) == ("weighted-estimator", True, 0.0)
        assert list(adapter.weights) == [0.5, 0.7, 1.0]
        assert list(predictions) == [1.0, 2.0, 3.0]

    def test_a_worse_weighted_refit_is_dropped(self):
        estimator, _, metrics, weighted = _weighted(_Adapter(np.array([9.0, 9.0, 9.0])), {"rmse": 2.0})

        assert (estimator, weighted, metrics) == ("estimator", False, {"rmse": 2.0})

    def test_a_failed_weighted_refit_keeps_the_candidate(self):
        adapter = MagicMock(algorithm="fake")
        adapter.fit.side_effect = TypeError("no sample_weight")

        estimator, _, _, weighted = _weighted(adapter, {"rmse": 2.0})

        assert (estimator, weighted) == ("estimator", False)
