"""
Unit tests for library/ml/backtest.py's run_backtest -- the tournament
runner itself, not any real algorithm. Fake adapters stand in for
XGBoost/LogisticRegression so these tests only verify run_backtest's own
orchestration (every candidate gets tuned/fit/evaluated, but only the
winner by promotion_metric gets a versioned artifact and gets promoted;
the winner's card carries what it competed against) -- library/ml/
test_model_types.py covers the real adapters, library/ml/
test_training_common.py covers save_model_artifact/promote_if_better
themselves.
"""
from unittest.mock import ANY, MagicMock, patch

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
    def test_every_candidate_is_evaluated_but_only_the_winner_is_saved(self):
        X_train, y_train, X_test, y_test = _xy()
        winner = _FakeAdapter("xgboost", np.array([0.9, 0.1, 0.9, 0.1]))  # perfect
        loser = _FakeAdapter("logistic_regression", np.array([0.4, 0.6, 0.4, 0.6]))  # backwards
        mock_s3 = MagicMock()

        def fake_save(s3, sport, model_name, algorithm, model_bytes, artifact_filename, metadata, summary_metrics):
            return {"model_name": model_name, "algorithm": algorithm, "version": 7, **metadata}

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

        # Both candidates get tuned/fit/evaluated -- the tournament itself
        # is unaffected by only saving the winner.
        assert winner.tune_and_fit_calls == 1
        assert loser.tune_and_fit_calls == 1
        # But only ONE artifact is ever written to S3 -- the loser is
        # scored and compared in memory only, never versioned.
        assert mock_save.call_count == 1
        mock_save.assert_called_once_with(
            mock_s3, "nfl", "win-probability", "xgboost", b"xgboost-estimator-bytes", "model.xgboost",
            ANY, ["accuracy", "log_loss"],
        )

        winner_card = result["winner"]
        assert winner_card["algorithm"] == "xgboost"
        # The winner's own metadata still carries every candidate's score,
        # including its own -- the comparison survives even though the
        # loser's own artifact doesn't.
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

        # The lower log_loss (winner) is promoted.
        mock_promote.assert_called_once_with(mock_s3, "nfl", "win-probability", winner_card["version"], winner_card, "log_loss")
        assert result["promoted"] is True
        # Top-level "candidates" is the lightweight score summary, not
        # full cards -- there's only ever one real card now.
        assert {c["algorithm"] for c in result["candidates"]} == {"xgboost", "logistic_regression"}

    def test_winner_card_carries_feature_columns_for_serving_to_read(self):
        """Regression test: model_loader.predict() (serving side) reads
        model_card["feature_columns"] to know which columns/order to build
        a live feature row into -- confirmed live this was missing from
        every promoted card (KeyError: 'feature_columns' on every real
        prediction request) since neither this function nor any of the
        three train_*.py callers ever added it."""
        X_train, y_train, X_test, y_test = _xy()
        adapter = _FakeAdapter("xgboost", np.array([0.9, 0.1, 0.9, 0.1]))

        def fake_save(s3, sport, model_name, algorithm, model_bytes, artifact_filename, metadata, summary_metrics):
            return {"model_name": model_name, "algorithm": algorithm, "version": 1, **metadata}

        with patch.object(backtest.training_common, "save_model_artifact", side_effect=fake_save), \
             patch.object(backtest.training_common, "promote_if_better", return_value=True):
            result = backtest.run_backtest(
                s3=MagicMock(), sport="nfl", model_name="win-probability", task="classification",
                X_train=X_train, y_train=y_train, X_test=X_test, y_test=y_test,
                candidates=[adapter],
                naive_baseline_metrics={},
                extra_metadata={"train_rows": 4, "test_rows": 4},
                summary_metrics=["accuracy", "log_loss"],
                promotion_metric="log_loss",
            )

        assert result["winner"]["feature_columns"] == ["a"]

    def test_candidates_expose_the_metric_that_actually_decided_ranking(self):
        """Regression test for a real, reported confusion: a run where the
        candidate with the HIGHER displayed accuracy wasn't promoted,
        because the actual gate metric (log_loss) favored a different,
        lower-accuracy candidate -- correct behavior (log_loss is the
        right rule for a probability output), but with only "score"
        (accuracy) visible on the raw model card, that looks like a bug.
        higher_accuracy is right on every sample but never confident
        (worse log_loss); lower_accuracy is confidently right on 4/5 and
        confidently WRONG on the 5th (fewer correct picks, but a much
        better log_loss overall)."""
        X = pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0, 5.0]})
        y = pd.Series([1, 0, 1, 0, 1])
        higher_accuracy = _FakeAdapter("logistic_regression", np.array([0.51, 0.49, 0.51, 0.49, 0.51]))
        lower_accuracy_better_log_loss = _FakeAdapter("xgboost", np.array([0.9, 0.1, 0.9, 0.1, 0.4]))

        def fake_save(s3, sport, model_name, algorithm, model_bytes, artifact_filename, metadata, summary_metrics):
            return {"model_name": model_name, "algorithm": algorithm, "version": 1, **metadata}

        with patch.object(backtest.training_common, "save_model_artifact", side_effect=fake_save), \
             patch.object(backtest.training_common, "promote_if_better", return_value=True):
            result = backtest.run_backtest(
                s3=MagicMock(), sport="nfl", model_name="win-probability", task="classification",
                X_train=X, y_train=y, X_test=X, y_test=y,
                candidates=[higher_accuracy, lower_accuracy_better_log_loss],
                naive_baseline_metrics={},
                extra_metadata={"train_rows": 5, "test_rows": 5},
                summary_metrics=["accuracy", "log_loss"],
                promotion_metric="log_loss",
            )

        # xgboost wins despite lower accuracy -- log_loss is the real rule.
        assert result["winner"]["algorithm"] == "xgboost"

        candidates = result["winner"]["candidates"]
        assert result["winner"]["candidates_ranked_by"] == "log_loss"
        by_algorithm = {c["algorithm"]: c for c in candidates}

        # "score" (accuracy) alone would make logistic_regression look like
        # it should have won -- rank_score (log_loss) is what actually
        # decided, and it's the opposite direction.
        assert by_algorithm["logistic_regression"]["score"] == 1.0
        assert by_algorithm["xgboost"]["score"] == 0.8
        assert by_algorithm["xgboost"]["rank_score"] < by_algorithm["logistic_regression"]["rank_score"]
        # Ranked best-first by rank_score, not by score.
        assert [c["algorithm"] for c in candidates] == ["xgboost", "logistic_regression"]

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

    def test_held_back_winner_still_gets_saved_but_stays_the_only_save(self):
        """A run's best candidate can still lose to promote_if_better's own
        comparison against whatever's ALREADY in production (a meaningful
        regression -- see PROMOTION_TOLERANCE) -- that's independent of
        this run's own tournament. Confirms the held-back case still only
        ever writes one artifact (this run's winner), not zero (the losing
        candidates never had one to begin with) and not more than one."""
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

        assert mock_save.call_count == 1
        assert result["winner"]["algorithm"] == "xgboost"
        assert result["promoted"] is False
        # The score summary still names both candidates even though only
        # the winner has a real artifact behind it.
        assert len(result["candidates"]) == 2
