from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import log_loss

from library.ml import backtest, calibration


def _overconfident(n=400, seed=7):
    """True chances, and a model's stated chances pushed toward 0/1."""
    rng = np.random.default_rng(seed)
    true = rng.uniform(0.2, 0.8, n)
    outcomes = (rng.uniform(size=n) < true).astype(int)
    stated = 1 / (1 + np.exp(-3 * np.log(true / (1 - true))))
    return stated, outcomes


class TestPlatt:
    def test_too_few_rows_or_one_outcome_gives_no_calibration(self):
        assert calibration.fit_platt([0.5] * 10, [1, 0] * 5) is None
        assert calibration.fit_platt([0.5] * 100, [1] * 100) is None

    def test_a_fitted_calibration_lowers_log_loss_of_overconfident_chances(self):
        stated, outcomes = _overconfident()

        fitted = calibration.fit_platt(stated, outcomes)

        assert fitted["method"] == "platt"
        assert fitted["slope"] < 1
        assert log_loss(outcomes, calibration.apply(fitted, stated)) < log_loss(outcomes, stated)

    def test_no_calibration_leaves_chances_unchanged(self):
        assert list(calibration.apply(None, [0.2, 0.9])) == [0.2, 0.9]


class _Adapter:
    algorithm = "fake"

    def __init__(self, predictions):
        self._predictions = predictions
        self.fit_rows = []

    def fit(self, X, y, params, sample_weight=None):
        self.fit_rows.append(len(X))
        return "estimator"

    def predict(self, estimator, X):
        return self._predictions[-len(X):]


class TestBacktestCalibration:
    def _split(self, stated, outcomes):
        X = pd.DataFrame({"a": np.arange(len(stated), dtype=float)})
        y = pd.Series(outcomes)
        return backtest.HoldoutSplit(X, y, X, y)

    def test_refits_on_the_earlier_rows_and_keeps_an_improving_calibration(self):
        stated, outcomes = _overconfident()
        split = self._split(stated, outcomes)
        adapter = _Adapter(stated)
        metrics = backtest._evaluate_for_task("classification", stated, split.y_test)

        predictions, calibrated_metrics, fitted = backtest._calibrated(adapter, {}, split, stated, metrics, "log_loss")

        assert adapter.fit_rows == [int(len(stated) * (1 - backtest.CALIBRATION_FRACTION))]
        assert fitted is not None
        assert calibrated_metrics["log_loss"] < metrics["log_loss"]
        assert list(predictions) == list(calibration.apply(fitted, stated))

    def test_a_calibration_that_does_not_help_is_dropped(self):
        stated, outcomes = _overconfident()
        split = self._split(stated, outcomes)
        metrics = {"log_loss": 0.0}

        predictions, kept_metrics, fitted = backtest._calibrated(_Adapter(stated), {}, split, stated, metrics, "log_loss")

        assert fitted is None
        assert kept_metrics == metrics
        assert predictions is stated

    def test_a_failed_refit_keeps_the_candidate_uncalibrated(self):
        stated, outcomes = _overconfident()
        adapter = MagicMock(algorithm="fake")
        adapter.fit.side_effect = ValueError("boom")

        assert backtest._fit_calibration(adapter, {}, *self._split(stated, outcomes)[:2]) is None


class TestServingCalibration:
    def test_model_loader_applies_the_cards_calibration(self):
        from library.serving import model_loader

        adapter = MagicMock()
        adapter.predict.return_value = np.array([0.9])
        card = {"algorithm": "fake", "feature_columns": ["a"], "calibration": {"method": "platt", "slope": 0.5, "intercept": 0.0}}

        with pytest.MonkeyPatch.context() as patcher:
            patcher.setitem(model_loader.ADAPTERS, "fake", adapter)
            value = model_loader.predict("estimator", card, {"a": 1.0})

        assert value == pytest.approx(float(calibration.apply(card["calibration"], [0.9])[0]))
