"""
Unit tests for library/ml/backtest.py's run_backtest -- the tournament
runner itself, not any real algorithm. Fake adapters stand in for
XGBoost/LogisticRegression so these tests only verify run_backtest's own
orchestration (every candidate gets tuned/fit/evaluated; the first
candidate of the run is promoted unconditionally; every candidate after
it only replaces what's live if it actually beats it; an interrupted-and-
resumed run skips candidates an earlier attempt already settled) --
library/ml/test_model_types.py covers the real adapters, library/ml/
test_training_common.py covers save_model_artifact/promote_if_better/
would_beat_current/force_promote/the run-progress helpers themselves.
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
             patch.object(backtest.training_common, "force_promote"), \
             patch.object(backtest.training_common, "would_beat_current", return_value=False):
            _run([first, second])

        # Tuning/fitting happens for every candidate regardless of who
        # ends up promoted -- the tournament itself is unaffected.
        assert first.tune_and_fit_calls == 1
        assert second.tune_and_fit_calls == 1

    def test_first_candidate_is_promoted_unconditionally(self):
        """The first candidate of a fresh run never goes through
        would_beat_current -- it's saved and force_promote'd no matter
        how it scores, since it's the run's earliest possible interruption-
        safe checkpoint."""
        # A "bad" first candidate on purpose -- backwards predictions.
        first = _FakeAdapter("logistic_regression", np.array([0.4, 0.6, 0.4, 0.6]))

        with patch.object(backtest.training_common, "save_model_artifact", side_effect=_fake_save) as mock_save, \
             patch.object(backtest.training_common, "force_promote") as mock_force_promote, \
             patch.object(backtest.training_common, "would_beat_current") as mock_would_beat:
            result = _run([first])

        mock_would_beat.assert_not_called()
        mock_save.assert_called_once()
        mock_force_promote.assert_called_once_with(ANY, "nfl", "win-probability", 7)
        assert [c["algorithm"] for c in result["promotions"]] == ["logistic_regression"]

    def test_later_candidate_only_promoted_if_it_beats_current(self):
        first = _FakeAdapter("xgboost", np.array([0.9, 0.1, 0.9, 0.1]))
        loses = _FakeAdapter("logistic_regression", np.array([0.4, 0.6, 0.4, 0.6]))
        wins = _FakeAdapter("random_forest_regressor", np.array([0.9, 0.1, 0.9, 0.1]))

        with patch.object(backtest.training_common, "save_model_artifact", side_effect=_fake_save) as mock_save, \
             patch.object(backtest.training_common, "force_promote"), \
             patch.object(backtest.training_common, "promote_if_better") as mock_promote, \
             patch.object(backtest.training_common, "would_beat_current", side_effect=[False, True]):
            result = _run([first, loses, wins])

        # first (force-promoted) + wins (beat current) both get saved;
        # loses does not.
        assert mock_save.call_count == 2
        saved_algorithms = {call.args[3] for call in mock_save.call_args_list}
        assert saved_algorithms == {"xgboost", "random_forest_regressor"}
        assert mock_promote.call_count == 1
        assert [c["algorithm"] for c in result["promotions"]] == ["xgboost", "random_forest_regressor"]

    def test_losing_candidate_is_never_persisted(self):
        first = _FakeAdapter("xgboost", np.array([0.9, 0.1, 0.9, 0.1]))
        loses = _FakeAdapter("logistic_regression", np.array([0.4, 0.6, 0.4, 0.6]))

        with patch.object(backtest.training_common, "save_model_artifact", side_effect=_fake_save) as mock_save, \
             patch.object(backtest.training_common, "force_promote"), \
             patch.object(backtest.training_common, "would_beat_current", return_value=False):
            result = _run([first, loses])

        # Only the first candidate (force-promoted) is ever saved.
        mock_save.assert_called_once()
        assert mock_save.call_args.args[3] == "xgboost"
        assert [c["algorithm"] for c in result["promotions"]] == ["xgboost"]
        # But the score summary still names both -- the comparison
        # survives even though the loser's own artifact doesn't.
        assert {c["algorithm"] for c in result["candidates"]} == {"xgboost", "logistic_regression"}

    def test_candidate_summary_carries_feature_columns_for_serving_to_read(self):
        """Regression test: model_loader.predict() (serving side) reads
        model_card["feature_columns"] to know which columns/order to build
        a live feature row into -- confirmed live this was missing from
        every promoted card (KeyError: 'feature_columns' on every real
        prediction request)."""
        adapter = _FakeAdapter("xgboost", np.array([0.9, 0.1, 0.9, 0.1]))

        with patch.object(backtest.training_common, "save_model_artifact", side_effect=_fake_save), \
             patch.object(backtest.training_common, "force_promote"):
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
             patch.object(backtest.training_common, "force_promote"), \
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
             patch.object(backtest.training_common, "force_promote"):
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

    def test_no_promotions_when_a_single_candidate_run_still_saves_the_first(self):
        """Even a 1-candidate run always saves+force-promotes that one
        candidate -- there's no such thing as an empty run producing zero
        promotions, since the first candidate of a fresh run is never
        gated."""
        adapter = _FakeAdapter("xgboost", np.array([0.9, 0.1, 0.9, 0.1]))

        with patch.object(backtest.training_common, "save_model_artifact", side_effect=_fake_save), \
             patch.object(backtest.training_common, "force_promote") as mock_force_promote:
            result = _run([adapter])

        mock_force_promote.assert_called_once()
        assert len(result["promotions"]) == 1

    def test_progress_saved_after_every_candidate_and_cleared_once_the_run_finishes(self):
        first = _FakeAdapter("xgboost", np.array([0.9, 0.1, 0.9, 0.1]))
        second = _FakeAdapter("logistic_regression", np.array([0.4, 0.6, 0.4, 0.6]))

        with patch.object(backtest.training_common, "load_run_progress", return_value=None), \
             patch.object(backtest.training_common, "save_run_progress") as mock_save_progress, \
             patch.object(backtest.training_common, "clear_run_progress") as mock_clear, \
             patch.object(backtest.training_common, "save_model_artifact", side_effect=_fake_save), \
             patch.object(backtest.training_common, "force_promote"), \
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
    interrupted attempt left off rather than redoing settled candidates
    or force-promoting a second time."""

    def test_candidates_already_settled_by_an_earlier_attempt_are_skipped(self):
        already_settled = {"algorithm": "xgboost", "score": 0.9, "rank_score": 0.05}
        new_candidate = _FakeAdapter("logistic_regression", np.array([0.4, 0.6, 0.4, 0.6]))

        with patch.object(backtest.training_common, "load_run_progress", return_value={
            "evaluated": [already_settled], "promotions": [], "force_promoted": True,
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

    def test_resumed_run_does_not_force_promote_a_second_time(self):
        """force_promoted=True (persisted from the interrupted attempt)
        means even the first NEW candidate this attempt evaluates is
        compared against current production like any later candidate --
        NOT force-promoted again just because it's first in this
        attempt's own loop."""
        first_new_candidate = _FakeAdapter("random_forest", np.array([0.9, 0.1, 0.9, 0.1]))

        with patch.object(backtest.training_common, "load_run_progress", return_value={
            "evaluated": [{"algorithm": "xgboost", "score": 0.9, "rank_score": 0.05}],
            "promotions": [{"algorithm": "xgboost", "version": 5}],
            "force_promoted": True,
        }), \
             patch.object(backtest.training_common, "save_run_progress"), \
             patch.object(backtest.training_common, "clear_run_progress"), \
             patch.object(backtest.training_common, "save_model_artifact", side_effect=_fake_save), \
             patch.object(backtest.training_common, "force_promote") as mock_force_promote, \
             patch.object(backtest.training_common, "promote_if_better") as mock_promote, \
             patch.object(backtest.training_common, "would_beat_current", return_value=True):
            result = backtest.run_backtest(
                s3=MagicMock(), sport="nfl", model_name="win-probability", task="classification",
                X_train=_xy()[0], y_train=_xy()[1], X_test=_xy()[2], y_test=_xy()[3],
                candidates=[first_new_candidate],
                naive_baseline_metrics={}, extra_metadata={"train_rows": 4, "test_rows": 4},
                summary_metrics=["accuracy", "log_loss"], promotion_metric="log_loss",
                run_id="run-1",
            )

        mock_force_promote.assert_not_called()
        mock_promote.assert_called_once()
        # promotions carries the earlier attempt's own promotion (loaded
        # from progress) plus this attempt's new one.
        assert [c["algorithm"] for c in result["promotions"]] == ["xgboost", "random_forest"]

    def test_resuming_a_run_where_every_candidate_was_already_settled_does_nothing_new(self):
        adapter = _FakeAdapter("xgboost", np.array([0.9, 0.1, 0.9, 0.1]))

        with patch.object(backtest.training_common, "load_run_progress", return_value={
            "evaluated": [{"algorithm": "xgboost", "score": 0.9, "rank_score": 0.05}],
            "promotions": [{"algorithm": "xgboost", "version": 5}],
            "force_promoted": True,
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
