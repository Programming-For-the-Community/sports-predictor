"""
Unit tests for library/ml/backtest.py's run_backtest -- the tournament
runner itself, not any real algorithm. Fake adapters stand in for
XGBoost/LogisticRegression so these tests only verify run_backtest's own
orchestration (every candidate gets tuned/fit/evaluated; every candidate,
including the first, is compared against whatever's currently live before
being persisted/promoted -- no force-promotion; an interrupted-and-
resumed run skips candidates an earlier attempt already settled) --
library/ml/test_model_types.py covers the real adapters, library/ml/
test_training_common.py covers save_model_artifact/promote_if_better/
would_beat_current/the run-progress helpers themselves.
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


def _fake_save(s3, sport, model_name, algorithm, model_bytes, artifact_filename, metadata, summary_metrics):
    return {"model_name": model_name, "algorithm": algorithm, "version": 7, **metadata}


def _run(candidates, run_id="run-1", **overrides):
    """Runs a FRESH tournament (no prior progress breadcrumb) -- see
    TestResumability for tests that start from an existing one."""
    kwargs = dict(
        s3=MagicMock(), sport="nfl", model_name="win-probability", task="classification",
        naive_baseline_metrics={}, extra_metadata={"train_rows": 4, "test_rows": 4},
        summary_metrics=["accuracy", "log_loss"], promotion_metric="log_loss",
        run_id=run_id,
    )
    kwargs.update(zip(["X_train", "y_train", "X_test", "y_test"], _xy()))
    kwargs["candidates"] = candidates
    kwargs.update(overrides)
    with patch.object(backtest.training_common, "load_run_progress", return_value=None), \
         patch.object(backtest.training_common, "save_run_progress"), \
         patch.object(backtest.training_common, "clear_run_progress"):
        return backtest.run_backtest(**kwargs)


class TestRunBacktest:
    def test_every_candidate_is_tuned_and_fit_regardless_of_outcome(self):
        first = _FakeAdapter("xgboost", np.array([0.9, 0.1, 0.9, 0.1]))  # perfect
        second = _FakeAdapter("logistic_regression", np.array([0.4, 0.6, 0.4, 0.6]))  # backwards

        with patch.object(backtest.training_common, "save_model_artifact", side_effect=_fake_save), \
             patch.object(backtest.training_common, "would_beat_current", return_value=False):
            _run([first, second])

        # Tuning/fitting happens for every candidate regardless of who
        # ends up promoted -- the tournament itself is unaffected.
        assert first.tune_and_fit_calls == 1
        assert second.tune_and_fit_calls == 1

    def test_first_candidate_goes_through_the_same_comparison_as_any_other(self):
        """No force-promotion -- the first candidate of a fresh run is
        compared against current production exactly like every later
        one, and is skipped (never persisted) if it doesn't win."""
        first = _FakeAdapter("logistic_regression", np.array([0.4, 0.6, 0.4, 0.6]))

        with patch.object(backtest.training_common, "save_model_artifact") as mock_save, \
             patch.object(backtest.training_common, "would_beat_current", return_value=False) as mock_would_beat:
            result = _run([first])

        mock_would_beat.assert_called_once()
        mock_save.assert_not_called()
        assert result["promotions"] == []

    def test_first_candidate_is_saved_and_promoted_when_it_wins(self):
        first = _FakeAdapter("xgboost", np.array([0.9, 0.1, 0.9, 0.1]))

        with patch.object(backtest.training_common, "save_model_artifact", side_effect=_fake_save) as mock_save, \
             patch.object(backtest.training_common, "would_beat_current", return_value=True), \
             patch.object(backtest.training_common, "promote_if_better") as mock_promote:
            result = _run([first])

        mock_save.assert_called_once()
        mock_promote.assert_called_once_with(ANY, "nfl", "win-probability", 7, ANY, "log_loss")
        assert [c["algorithm"] for c in result["promotions"]] == ["xgboost"]

    def test_later_candidate_only_promoted_if_it_beats_current(self):
        first = _FakeAdapter("xgboost", np.array([0.9, 0.1, 0.9, 0.1]))
        loses = _FakeAdapter("logistic_regression", np.array([0.4, 0.6, 0.4, 0.6]))
        wins = _FakeAdapter("random_forest_regressor", np.array([0.9, 0.1, 0.9, 0.1]))

        with patch.object(backtest.training_common, "save_model_artifact", side_effect=_fake_save) as mock_save, \
             patch.object(backtest.training_common, "promote_if_better") as mock_promote, \
             patch.object(backtest.training_common, "would_beat_current", side_effect=[True, False, True]):
            result = _run([first, loses, wins])

        # first and wins both beat whatever was live when they ran; loses
        # does not.
        assert mock_save.call_count == 2
        saved_algorithms = {call.args[3] for call in mock_save.call_args_list}
        assert saved_algorithms == {"xgboost", "random_forest_regressor"}
        assert mock_promote.call_count == 2
        assert [c["algorithm"] for c in result["promotions"]] == ["xgboost", "random_forest_regressor"]

    def test_losing_candidate_is_never_persisted(self):
        first = _FakeAdapter("xgboost", np.array([0.9, 0.1, 0.9, 0.1]))
        loses = _FakeAdapter("logistic_regression", np.array([0.4, 0.6, 0.4, 0.6]))

        with patch.object(backtest.training_common, "save_model_artifact", side_effect=_fake_save) as mock_save, \
             patch.object(backtest.training_common, "promote_if_better"), \
             patch.object(backtest.training_common, "would_beat_current", side_effect=[True, False]):
            result = _run([first, loses])

        # Only the winning first candidate is ever saved.
        mock_save.assert_called_once()
        assert mock_save.call_args.args[3] == "xgboost"
        assert [c["algorithm"] for c in result["promotions"]] == ["xgboost"]
        # But the score summary still names both -- the comparison
        # survives even though the loser's own artifact doesn't.
        assert {c["algorithm"] for c in result["candidates"]} == {"xgboost", "logistic_regression"}

    def test_a_losing_first_candidate_produces_zero_promotions(self):
        """A single-candidate run that loses now genuinely promotes
        nothing -- there's no longer a force-promotion guarantee that a
        fresh run always ends with something new live."""
        adapter = _FakeAdapter("xgboost", np.array([0.4, 0.6, 0.4, 0.6]))  # backwards

        with patch.object(backtest.training_common, "save_model_artifact") as mock_save, \
             patch.object(backtest.training_common, "would_beat_current", return_value=False):
            result = _run([adapter])

        mock_save.assert_not_called()
        assert result["promotions"] == []

    def test_candidate_summary_carries_feature_columns_for_serving_to_read(self):
        """Regression test: model_loader.predict() (serving side) reads
        model_card["feature_columns"] to know which columns/order to build
        a live feature row into -- confirmed live this was missing from
        every promoted card (KeyError: 'feature_columns' on every real
        prediction request)."""
        adapter = _FakeAdapter("xgboost", np.array([0.9, 0.1, 0.9, 0.1]))

        with patch.object(backtest.training_common, "save_model_artifact", side_effect=_fake_save), \
             patch.object(backtest.training_common, "would_beat_current", return_value=True), \
             patch.object(backtest.training_common, "promote_if_better"):
            result = _run([adapter])

        assert result["promotions"][0]["feature_columns"] == ["a"]

    def test_candidates_expose_the_metric_that_actually_decided_ranking(self):
        """Regression test for a real, reported confusion: a run where the
        candidate with the HIGHER displayed accuracy wasn't promoted,
        because the actual gate metric (log_loss) favored a different,
        lower-accuracy candidate -- correct behavior (log_loss is the
        right rule for a probability output), but with only "score"
        (accuracy) visible on the raw model card, that looks like a bug."""
        X = pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0, 5.0]})
        y = pd.Series([1, 0, 1, 0, 1])
        higher_accuracy = _FakeAdapter("logistic_regression", np.array([0.51, 0.49, 0.51, 0.49, 0.51]))
        lower_accuracy_better_log_loss = _FakeAdapter("xgboost", np.array([0.9, 0.1, 0.9, 0.1, 0.4]))

        with patch.object(backtest.training_common, "save_model_artifact", side_effect=_fake_save), \
             patch.object(backtest.training_common, "would_beat_current", return_value=False):
            result = _run(
                [higher_accuracy, lower_accuracy_better_log_loss],
                X_train=X, y_train=y, X_test=X, y_test=y,
            )

        by_algorithm = {c["algorithm"]: c for c in result["candidates"]}
        assert by_algorithm["logistic_regression"]["score"] == 1.0
        assert by_algorithm["xgboost"]["score"] == 0.8
        assert by_algorithm["xgboost"]["rank_score"] < by_algorithm["logistic_regression"]["rank_score"]
        # Ranked best-first by rank_score, not by score.
        assert [c["algorithm"] for c in result["candidates"]] == ["xgboost", "logistic_regression"]

    def test_regression_task_uses_rmse_mae_not_accuracy_log_loss(self):
        X_train = pd.DataFrame({"a": [1.0, 2.0]})
        y_train = pd.Series([10.0, 20.0])
        X_test = pd.DataFrame({"a": [1.0, 2.0]})
        y_test = pd.Series([12.0, 18.0])
        adapter = _FakeAdapter("xgboost", np.array([10.0, 20.0]))

        with patch.object(backtest.training_common, "save_model_artifact", side_effect=_fake_save), \
             patch.object(backtest.training_common, "would_beat_current", return_value=True), \
             patch.object(backtest.training_common, "promote_if_better"):
            result = _run(
                [adapter], sport="nfl", model_name="score-margin",
                X_train=X_train, y_train=y_train, X_test=X_test, y_test=y_test,
                naive_baseline_metrics={"naive_baseline_rmse": 5.0, "naive_baseline_mae": 4.0},
                extra_metadata={"train_rows": 2, "test_rows": 2},
                summary_metrics=["rmse", "mae"], promotion_metric="rmse", task="regression",
            )

        card = result["promotions"][0]
        assert "rmse" in card
        assert "mae" in card
        assert "accuracy" not in card
        assert card["naive_baseline_rmse"] == 5.0
        # Regression candidates display mae (a real +/- error range in the
        # target's own units), not rmse.
        assert result["candidates"][0]["score"] == card["mae"]
        assert "rmse" not in result["candidates"][0]

    def test_promoting_a_candidate_worse_than_baseline_logs_a_warning_but_still_promotes(self):
        # A perfect-vs-baseline candidate deliberately scored WORSE than
        # naive_baseline_rmse -- still wins (beats current production,
        # which is what would_beat_current is mocked to say) and still
        # gets promoted, just with a loud warning logged. See
        # backtest._is_worse_than_baseline / run_backtest's own docstring
        # for why this is warn-only, not a hard block.
        X_train = pd.DataFrame({"a": [1.0, 2.0]})
        y_train = pd.Series([10.0, 20.0])
        X_test = pd.DataFrame({"a": [1.0, 2.0]})
        y_test = pd.Series([12.0, 18.0])
        adapter = _FakeAdapter("xgboost", np.array([10.0, 20.0]))  # rmse=2.0

        with patch.object(backtest.training_common, "save_model_artifact", side_effect=_fake_save), \
             patch.object(backtest.training_common, "would_beat_current", return_value=True), \
             patch.object(backtest.training_common, "promote_if_better"), \
             patch.object(backtest, "logger") as mock_logger:
            result = _run(
                [adapter], sport="nfl", model_name="score-margin",
                X_train=X_train, y_train=y_train, X_test=X_test, y_test=y_test,
                naive_baseline_metrics={"naive_baseline_rmse": 1.0, "naive_baseline_mae": 1.0},
                extra_metadata={"train_rows": 2, "test_rows": 2},
                summary_metrics=["rmse", "mae"], promotion_metric="rmse", task="regression",
            )

        mock_logger.warning.assert_called_once()
        assert len(result["promotions"]) == 1

    def test_promoting_a_candidate_better_than_baseline_does_not_warn(self):
        X_train = pd.DataFrame({"a": [1.0, 2.0]})
        y_train = pd.Series([10.0, 20.0])
        X_test = pd.DataFrame({"a": [1.0, 2.0]})
        y_test = pd.Series([12.0, 18.0])
        adapter = _FakeAdapter("xgboost", np.array([10.0, 20.0]))  # rmse=2.0

        with patch.object(backtest.training_common, "save_model_artifact", side_effect=_fake_save), \
             patch.object(backtest.training_common, "would_beat_current", return_value=True), \
             patch.object(backtest.training_common, "promote_if_better"), \
             patch.object(backtest, "logger") as mock_logger:
            _run(
                [adapter], sport="nfl", model_name="score-margin",
                X_train=X_train, y_train=y_train, X_test=X_test, y_test=y_test,
                naive_baseline_metrics={"naive_baseline_rmse": 10.0, "naive_baseline_mae": 10.0},
                extra_metadata={"train_rows": 2, "test_rows": 2},
                summary_metrics=["rmse", "mae"], promotion_metric="rmse", task="regression",
            )

        mock_logger.warning.assert_not_called()

    def test_progress_saved_after_every_candidate_and_cleared_once_the_run_finishes(self):
        first = _FakeAdapter("xgboost", np.array([0.9, 0.1, 0.9, 0.1]))
        second = _FakeAdapter("logistic_regression", np.array([0.4, 0.6, 0.4, 0.6]))

        with patch.object(backtest.training_common, "load_run_progress", return_value=None), \
             patch.object(backtest.training_common, "save_run_progress") as mock_save_progress, \
             patch.object(backtest.training_common, "clear_run_progress") as mock_clear, \
             patch.object(backtest.training_common, "save_model_artifact", side_effect=_fake_save), \
             patch.object(backtest.training_common, "would_beat_current", return_value=False):
            backtest.run_backtest(
                s3=MagicMock(), sport="nfl", model_name="win-probability", task="classification",
                X_train=_xy()[0], y_train=_xy()[1], X_test=_xy()[2], y_test=_xy()[3],
                candidates=[first, second],
                naive_baseline_metrics={}, extra_metadata={"train_rows": 4, "test_rows": 4},
                summary_metrics=["accuracy", "log_loss"], promotion_metric="log_loss",
                run_id="run-1",
            )

        # Once after each of the 2 candidates -- a task interrupted right
        # after either one resumes without redoing it.
        assert mock_save_progress.call_count == 2
        mock_clear.assert_called_once_with(ANY, "nfl", "win-probability", "run-1")


class TestResumability:
    """A relaunched task (Fargate Spot Retry, or the Catch-driven
    RunTrainingTaskOnDemand fallback) calls run_backtest again with the
    SAME run_id -- these tests verify it picks up where an earlier,
    interrupted attempt left off rather than redoing settled candidates."""

    def test_candidates_already_settled_by_an_earlier_attempt_are_skipped(self):
        already_settled = {"algorithm": "xgboost", "score": 0.9, "rank_score": 0.05}
        new_candidate = _FakeAdapter("logistic_regression", np.array([0.4, 0.6, 0.4, 0.6]))

        with patch.object(backtest.training_common, "load_run_progress", return_value={
            "evaluated": [already_settled], "promotions": [],
        }), \
             patch.object(backtest.training_common, "save_run_progress"), \
             patch.object(backtest.training_common, "clear_run_progress"), \
             patch.object(backtest.training_common, "save_model_artifact", side_effect=_fake_save), \
             patch.object(backtest.training_common, "would_beat_current", return_value=False):
            already_ran = _FakeAdapter("xgboost", np.array([0.9, 0.1, 0.9, 0.1]))
            result = backtest.run_backtest(
                s3=MagicMock(), sport="nfl", model_name="win-probability", task="classification",
                X_train=_xy()[0], y_train=_xy()[1], X_test=_xy()[2], y_test=_xy()[3],
                candidates=[already_ran, new_candidate],
                naive_baseline_metrics={}, extra_metadata={"train_rows": 4, "test_rows": 4},
                summary_metrics=["accuracy", "log_loss"], promotion_metric="log_loss",
                run_id="run-1",
            )

        # already_ran matches the settled entry's algorithm -- never
        # re-tuned/fit.
        assert already_ran.tune_and_fit_calls == 0
        assert new_candidate.tune_and_fit_calls == 1
        assert {c["algorithm"] for c in result["candidates"]} == {"xgboost", "logistic_regression"}

    def test_resumed_run_compares_the_first_new_candidate_like_any_other(self):
        """No force-promotion concept left to special-case on resume --
        the first NEW candidate a resumed attempt evaluates is compared
        to current production exactly like any other candidate, same as
        a fresh run's own first candidate now is."""
        first_new_candidate = _FakeAdapter("random_forest", np.array([0.9, 0.1, 0.9, 0.1]))

        with patch.object(backtest.training_common, "load_run_progress", return_value={
            "evaluated": [{"algorithm": "xgboost", "score": 0.9, "rank_score": 0.05}],
            "promotions": [{"algorithm": "xgboost", "version": 5}],
        }), \
             patch.object(backtest.training_common, "save_run_progress"), \
             patch.object(backtest.training_common, "clear_run_progress"), \
             patch.object(backtest.training_common, "save_model_artifact", side_effect=_fake_save), \
             patch.object(backtest.training_common, "promote_if_better") as mock_promote, \
             patch.object(backtest.training_common, "would_beat_current", return_value=True) as mock_would_beat:
            result = backtest.run_backtest(
                s3=MagicMock(), sport="nfl", model_name="win-probability", task="classification",
                X_train=_xy()[0], y_train=_xy()[1], X_test=_xy()[2], y_test=_xy()[3],
                candidates=[first_new_candidate],
                naive_baseline_metrics={}, extra_metadata={"train_rows": 4, "test_rows": 4},
                summary_metrics=["accuracy", "log_loss"], promotion_metric="log_loss",
                run_id="run-1",
            )

        mock_would_beat.assert_called_once()
        mock_promote.assert_called_once()
        # promotions carries the earlier attempt's own promotion (loaded
        # from progress) plus this attempt's new one.
        assert [c["algorithm"] for c in result["promotions"]] == ["xgboost", "random_forest"]

    def test_resuming_a_run_where_every_candidate_was_already_settled_does_nothing_new(self):
        adapter = _FakeAdapter("xgboost", np.array([0.9, 0.1, 0.9, 0.1]))

        with patch.object(backtest.training_common, "load_run_progress", return_value={
            "evaluated": [{"algorithm": "xgboost", "score": 0.9, "rank_score": 0.05}],
            "promotions": [{"algorithm": "xgboost", "version": 5}],
        }), \
             patch.object(backtest.training_common, "save_run_progress") as mock_save_progress, \
             patch.object(backtest.training_common, "clear_run_progress") as mock_clear, \
             patch.object(backtest.training_common, "save_model_artifact") as mock_save:
            result = backtest.run_backtest(
                s3=MagicMock(), sport="nfl", model_name="win-probability", task="classification",
                X_train=_xy()[0], y_train=_xy()[1], X_test=_xy()[2], y_test=_xy()[3],
                candidates=[adapter],
                naive_baseline_metrics={}, extra_metadata={"train_rows": 4, "test_rows": 4},
                summary_metrics=["accuracy", "log_loss"], promotion_metric="log_loss",
                run_id="run-1",
            )

        assert adapter.tune_and_fit_calls == 0
        mock_save.assert_not_called()
        mock_save_progress.assert_not_called()
        mock_clear.assert_called_once()
        assert [c["algorithm"] for c in result["promotions"]] == ["xgboost"]


class TestIsWorseThanBaseline:
    def test_true_when_the_metrics_own_baseline_is_worse(self):
        assert backtest._is_worse_than_baseline({"rmse": 5.0}, {"naive_baseline_rmse": 4.0}, "rmse") is True

    def test_false_when_the_metrics_own_baseline_is_better_or_tied(self):
        assert backtest._is_worse_than_baseline({"rmse": 4.0}, {"naive_baseline_rmse": 4.0}, "rmse") is False
        assert backtest._is_worse_than_baseline({"rmse": 3.0}, {"naive_baseline_rmse": 4.0}, "rmse") is False

    def test_log_loss_falls_back_to_accuracy_vs_baseline_accuracy(self):
        # win-probability's own naive_baseline_metrics never computes a
        # baseline log_loss (see train_win_probability_model.py's own
        # SUMMARY_METRICS) -- accuracy is the only baseline signal it has.
        metadata = {"log_loss": 0.6, "accuracy": 0.55}
        assert backtest._is_worse_than_baseline(metadata, {"naive_baseline_accuracy": 0.6}, "log_loss") is True
        assert backtest._is_worse_than_baseline(metadata, {"naive_baseline_accuracy": 0.5}, "log_loss") is False

    def test_none_when_no_comparable_baseline_exists_at_all(self):
        assert backtest._is_worse_than_baseline({"log_loss": 0.6}, {}, "log_loss") is None
