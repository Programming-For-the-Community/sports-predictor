"""
Unit tests for library/ml/backtest.py's run_backtest -- the tournament
runner itself, not any real algorithm. Fake adapters stand in for
XGBoost/LogisticRegression so these tests only verify run_backtest's own
orchestration (every candidate gets evaluated and versioned, the best one
by promotion_metric gets promoted, every card carries what it competed
against) -- library/ml/test_model_types.py covers the real adapters,
library/ml/test_training_common.py covers save_model_artifact/
promote_if_better themselves.
"""
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from library.ml import backtest


class _FakeAdapter:
    def __init__(self, algorithm, predictions, params=None):
        self.algorithm = algorithm
        self.artifact_filename = f"model.{algorithm}"
        self._predictions = predictions
        self._params = params or {}
        self.tune_and_fit_calls = 0

    def tune_and_fit(self, X_train, y_train):
        self.tune_and_fit_calls += 1
        return f"{self.algorithm}-estimator", self._params

    def predict(self, estimator, X):
        return self._predictions

    def feature_importances(self, estimator, feature_columns):
        return {"a": 1.0}

    def serialize(self, estimator):
        return f"{estimator}-bytes".encode()


def _xy():
    X = pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0]})
    y = pd.Series([1, 0, 1, 0])
    return X, y, X, y


class TestRunBacktest:
    def test_every_candidate_gets_tuned_evaluated_and_written(self):
        X_train, y_train, X_test, y_test = _xy()
        winner = _FakeAdapter("xgboost", np.array([0.9, 0.1, 0.9, 0.1]))  # perfect
        loser = _FakeAdapter("logistic_regression", np.array([0.4, 0.6, 0.4, 0.6]))  # backwards
        mock_s3 = MagicMock()

        cards_by_algorithm = {}

        def fake_save(s3, sport, model_name, algorithm, model_bytes, artifact_filename, metadata, summary_metrics):
            card = {"model_name": model_name, "algorithm": algorithm, "version": len(cards_by_algorithm) + 1, **metadata}
            cards_by_algorithm[algorithm] = card
            return card

        with patch.object(backtest.training_common, "save_model_artifact", side_effect=fake_save) as mock_save, \
             patch.object(backtest.training_common, "promote_if_better", return_value=True) as mock_promote:
            result = backtest.run_backtest(
                s3=mock_s3, sport="nfl", model_name="win-probability", task="classification",
                X_train=X_train, y_train=y_train, X_test=X_test, y_test=y_test,
                candidates=[winner, loser],
                naive_baseline_metrics={"naive_baseline_accuracy": 0.5},
                extra_metadata={"train_rows": 4, "test_rows": 4},
                summary_metrics=["accuracy", "log_loss"],
                promotion_metric="log_loss",
            )

        assert winner.tune_and_fit_calls == 1
        assert loser.tune_and_fit_calls == 1
        assert mock_save.call_count == 2

        # Both candidates' metadata carries every OTHER candidate's score too.
        winner_card = cards_by_algorithm["xgboost"]
        assert {c["algorithm"] for c in winner_card["candidates"]} == {"xgboost", "logistic_regression"}
        assert winner_card["naive_baseline_accuracy"] == 0.5
        assert winner_card["train_rows"] == 4

        # "score" is accuracy for a classification task (human-readable),
        # NOT the log_loss actually used to rank/promote -- xgboost's
        # predictions are perfect (accuracy 1.0), logistic's are exactly
        # backwards (accuracy 0.0). Higher accuracy wins even though the
        # underlying ranking rule (log_loss, lower-is-better) is what
        # actually decided the order.
        by_algorithm = {c["algorithm"]: c["score"] for c in winner_card["candidates"]}
        assert by_algorithm["xgboost"] == 1.0
        assert by_algorithm["logistic_regression"] == 0.0
        assert all("log_loss" not in c for c in winner_card["candidates"])
        # Ranked best-first by the real gate metric.
        assert [c["algorithm"] for c in winner_card["candidates"]] == ["xgboost", "logistic_regression"]

        # The lower log_loss (winner) is promoted, not the loser.
        mock_promote.assert_called_once_with(mock_s3, "nfl", "win-probability", winner_card["version"], winner_card, "log_loss")
        assert result["winner"]["algorithm"] == "xgboost"
        assert result["promoted"] is True
        assert {c["algorithm"] for c in result["candidates"]} == {"xgboost", "logistic_regression"}

    def test_promotes_whichever_candidate_scores_best_on_the_promotion_metric(self):
        X_train, y_train, X_test, y_test = _xy()
        better = _FakeAdapter("xgboost", np.array([0.9, 0.1, 0.9, 0.1]))
        worse = _FakeAdapter("logistic_regression", np.array([0.6, 0.4, 0.6, 0.4]))

        def fake_save(s3, sport, model_name, algorithm, model_bytes, artifact_filename, metadata, summary_metrics):
            return {"model_name": model_name, "algorithm": algorithm, "version": 1, **metadata}

        with patch.object(backtest.training_common, "save_model_artifact", side_effect=fake_save), \
             patch.object(backtest.training_common, "promote_if_better", return_value=True) as mock_promote:
            result = backtest.run_backtest(
                s3=MagicMock(), sport="nfl", model_name="win-probability", task="classification",
                X_train=X_train, y_train=y_train, X_test=X_test, y_test=y_test,
                candidates=[worse, better],
                naive_baseline_metrics={},
                extra_metadata={"train_rows": 4, "test_rows": 4},
                summary_metrics=["accuracy", "log_loss"],
                promotion_metric="log_loss",
            )

        assert result["winner"]["algorithm"] == "xgboost"
        promote_call = mock_promote.call_args.args
        assert promote_call[3] == result["winner"]["version"]  # version
        assert promote_call[4]["algorithm"] == "xgboost"  # metadata passed is the winner's own card

    def test_regression_task_uses_rmse_mae_not_accuracy_log_loss(self):
        X_train = pd.DataFrame({"a": [1.0, 2.0]})
        y_train = pd.Series([10.0, 20.0])
        X_test = pd.DataFrame({"a": [1.0, 2.0]})
        y_test = pd.Series([12.0, 18.0])
        adapter = _FakeAdapter("xgboost", np.array([10.0, 20.0]))

        def fake_save(s3, sport, model_name, algorithm, model_bytes, artifact_filename, metadata, summary_metrics):
            return {"model_name": model_name, "algorithm": algorithm, "version": 1, **metadata}

        with patch.object(backtest.training_common, "save_model_artifact", side_effect=fake_save), \
             patch.object(backtest.training_common, "promote_if_better", return_value=True):
            result = backtest.run_backtest(
                s3=MagicMock(), sport="nfl", model_name="score-margin", task="regression",
                X_train=X_train, y_train=y_train, X_test=X_test, y_test=y_test,
                candidates=[adapter],
                naive_baseline_metrics={"naive_baseline_rmse": 5.0, "naive_baseline_mae": 4.0},
                extra_metadata={"train_rows": 2, "test_rows": 2},
                summary_metrics=["rmse", "mae"],
                promotion_metric="rmse",
            )

        assert "rmse" in result["winner"]
        assert "mae" in result["winner"]
        assert "accuracy" not in result["winner"]
        assert result["winner"]["naive_baseline_rmse"] == 5.0

        # Regression candidates display mae (a real +/- error range in the
        # target's own units), not rmse -- the ranking rule and the
        # display value are deliberately different metrics here.
        assert result["winner"]["candidates"][0]["score"] == result["winner"]["mae"]
        assert "rmse" not in result["winner"]["candidates"][0]

    def test_nothing_is_discarded_every_candidate_stays_versioned(self):
        X_train, y_train, X_test, y_test = _xy()
        a = _FakeAdapter("xgboost", np.array([0.9, 0.1, 0.9, 0.1]))
        b = _FakeAdapter("logistic_regression", np.array([0.4, 0.6, 0.4, 0.6]))

        def fake_save(s3, sport, model_name, algorithm, model_bytes, artifact_filename, metadata, summary_metrics):
            return {"model_name": model_name, "algorithm": algorithm, "version": 1, **metadata}

        with patch.object(backtest.training_common, "save_model_artifact", side_effect=fake_save) as mock_save, \
             patch.object(backtest.training_common, "promote_if_better", return_value=False):
            result = backtest.run_backtest(
                s3=MagicMock(), sport="nfl", model_name="win-probability", task="classification",
                X_train=X_train, y_train=y_train, X_test=X_test, y_test=y_test,
                candidates=[a, b],
                naive_baseline_metrics={},
                extra_metadata={"train_rows": 4, "test_rows": 4},
                summary_metrics=["accuracy", "log_loss"],
                promotion_metric="log_loss",
            )

        # save_model_artifact ran for both, even though the winner didn't
        # end up getting promoted over an even-better existing production
        # version -- held-back candidates still get a versioned artifact.
        assert mock_save.call_count == 2
        assert result["promoted"] is False
        assert len(result["candidates"]) == 2
