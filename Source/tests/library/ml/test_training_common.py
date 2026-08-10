"""
Unit tests for library/ml/training_common.py -- moved and adapted from
Source/tests/model-training/nfl/test_model_common.py now that `sport` is
an explicit parameter instead of a hardcoded module constant (see
training_common.py's own docstring for why). evaluate_holdout/
evaluate_regression_holdout now take raw predictions/probabilities
instead of a model object -- covered directly here rather than only
indirectly through a training script's own tests, since they're no
longer coupled to any one script.
"""
from unittest.mock import MagicMock

import numpy as np

from library.ml import training_common


class TestEvaluateHoldout:
    def test_computes_accuracy_and_log_loss_from_probabilities(self):
        probabilities = np.array([0.9, 0.1, 0.8, 0.2])
        y_test = [1, 0, 0, 0]  # 3rd row wrong at the 0.5 threshold

        metrics = training_common.evaluate_holdout(probabilities, y_test)

        assert metrics["accuracy"] == 0.75
        assert isinstance(metrics["log_loss"], float)

    def test_thresholds_at_point_five(self):
        probabilities = np.array([0.5, 0.49])
        y_test = [1, 0]

        metrics = training_common.evaluate_holdout(probabilities, y_test)

        assert metrics["accuracy"] == 1.0


class TestEvaluateRegressionHoldout:
    def test_computes_rmse_and_mae(self):
        predictions = np.array([10.0, 20.0])
        y_test = [12.0, 18.0]

        metrics = training_common.evaluate_regression_holdout(predictions, y_test)

        assert metrics["mae"] == 2.0
        assert isinstance(metrics["rmse"], float)


class TestGetCurrentVersion:
    def test_returns_none_when_no_pointer_exists(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = False

        assert training_common.get_current_version(mock_s3, "nfl", "win-probability") is None

    def test_returns_the_pointed_at_version(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = True
        mock_s3.get_json.return_value = {"version": 4}

        version = training_common.get_current_version(mock_s3, "nfl", "win-probability")

        assert version == 4
        mock_s3.get_json.assert_called_once_with("nfl/win-probability/current.json")

    def test_scopes_the_pointer_key_to_the_given_sport(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = True
        mock_s3.get_json.return_value = {"version": 1}

        training_common.get_current_version(mock_s3, "nba", "win-probability")

        mock_s3.get_json.assert_called_once_with("nba/win-probability/current.json")


class TestPromoteIfBetter:
    def test_promotes_directly_when_nothing_is_currently_promoted(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = False

        promoted = training_common.promote_if_better(mock_s3, "nfl", "win-probability", 1, {"log_loss": 0.65}, "log_loss")

        assert promoted is True
        mock_s3.put_json.assert_called_once_with("nfl/win-probability/current.json", {"version": 1})

    def test_promotes_when_strictly_better_than_current(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = True
        mock_s3.get_json.return_value = {"version": 5, "log_loss": 0.65}

        promoted = training_common.promote_if_better(mock_s3, "nfl", "win-probability", 6, {"log_loss": 0.60}, "log_loss")

        assert promoted is True
        mock_s3.put_json.assert_called_once_with("nfl/win-probability/current.json", {"version": 6})

    def test_promotes_when_within_tolerance_but_slightly_worse(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = True
        mock_s3.get_json.return_value = {"version": 5, "log_loss": 0.620}

        promoted = training_common.promote_if_better(mock_s3, "nfl", "win-probability", 6, {"log_loss": 0.626}, "log_loss")

        assert promoted is True

    def test_holds_back_a_meaningful_regression(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = True
        mock_s3.get_json.return_value = {"version": 5, "log_loss": 0.60}

        promoted = training_common.promote_if_better(mock_s3, "nfl", "win-probability", 6, {"log_loss": 0.70}, "log_loss")

        assert promoted is False
        mock_s3.put_json.assert_not_called()

    def test_reads_the_current_versions_own_model_card_for_comparison(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = True
        mock_s3.get_json.return_value = {"version": 5, "log_loss": 0.62}

        training_common.promote_if_better(mock_s3, "nfl", "win-probability", 6, {"log_loss": 0.61}, "log_loss")

        mock_s3.get_json.assert_called_with("nfl/win-probability/v5/model_card.json")

    def test_gate_metric_is_parameterized_not_hardcoded_to_log_loss(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = True
        mock_s3.get_json.return_value = {"version": 1, "rmse": 40.0}

        promoted = training_common.promote_if_better(
            mock_s3, "nfl", "player-prop-passing-yards", 2, {"rmse": 45.0}, "rmse",
        )

        assert promoted is False
        mock_s3.put_json.assert_not_called()

    def test_compares_across_different_algorithms_with_no_special_casing(self):
        # The currently-promoted version was xgboost; the challenger is
        # logistic_regression -- promote_if_better only ever compares the
        # two versions' own metric value, never algorithm identity.
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = True
        mock_s3.get_json.return_value = {"version": 5, "algorithm": "xgboost", "log_loss": 0.65}

        promoted = training_common.promote_if_better(
            mock_s3, "nfl", "win-probability", 6, {"algorithm": "logistic_regression", "log_loss": 0.60}, "log_loss",
        )

        assert promoted is True

    def test_scopes_the_pointer_key_to_the_given_sport(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = False

        training_common.promote_if_better(mock_s3, "nba", "win-probability", 1, {"log_loss": 0.65}, "log_loss")

        mock_s3.put_json.assert_called_once_with("nba/win-probability/current.json", {"version": 1})


class TestWouldBeatCurrent:
    """Read-only counterpart to promote_if_better -- used by
    library.ml.backtest.run_backtest to decide whether a candidate is
    worth persisting at all BEFORE writing anything to S3."""

    def test_true_when_nothing_is_currently_promoted(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = False

        assert training_common.would_beat_current(mock_s3, "nfl", "win-probability", {"log_loss": 0.65}, "log_loss") is True
        mock_s3.put_json.assert_not_called()

    def test_true_when_strictly_better_than_current(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = True
        mock_s3.get_json.return_value = {"version": 5, "log_loss": 0.65}

        assert training_common.would_beat_current(mock_s3, "nfl", "win-probability", {"log_loss": 0.60}, "log_loss") is True

    def test_false_for_a_meaningful_regression(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = True
        mock_s3.get_json.return_value = {"version": 5, "log_loss": 0.60}

        assert training_common.would_beat_current(mock_s3, "nfl", "win-probability", {"log_loss": 0.70}, "log_loss") is False

    def test_never_writes_anything(self):
        """The whole point -- run_backtest calls this before deciding
        whether to persist a candidate at all, so it must never itself
        write to S3, win or lose."""
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = True
        mock_s3.get_json.return_value = {"version": 5, "log_loss": 0.60}

        training_common.would_beat_current(mock_s3, "nfl", "win-probability", {"log_loss": 0.55}, "log_loss")
        training_common.would_beat_current(mock_s3, "nfl", "win-probability", {"log_loss": 0.90}, "log_loss")

        mock_s3.put_json.assert_not_called()


class TestForcePromote:
    """Unconditional pointer flip -- used by run_backtest for the first
    candidate in a tournament run only, so a run interrupted after one
    candidate still leaves that version live."""

    def test_writes_the_pointer_with_no_comparison(self):
        mock_s3 = MagicMock()

        training_common.force_promote(mock_s3, "nfl", "win-probability", 6)

        mock_s3.put_json.assert_called_once_with("nfl/win-probability/current.json", {"version": 6})
        # Unlike promote_if_better, force_promote never reads anything to
        # compare against first.
        mock_s3.get_json.assert_not_called()

    def test_scopes_the_pointer_key_to_the_given_sport(self):
        mock_s3 = MagicMock()

        training_common.force_promote(mock_s3, "nba", "win-probability", 1)

        mock_s3.put_json.assert_called_once_with("nba/win-probability/current.json", {"version": 1})


class TestResolveRunId:
    """Threaded through to library.ml.backtest.run_backtest as its
    resumable-progress breadcrumb key -- see load_run_progress/
    save_run_progress/clear_run_progress below."""

    def test_uses_training_run_id_env_var_when_set(self, monkeypatch):
        monkeypatch.setenv("TRAINING_RUN_ID", "sfn-execution-abc123")

        assert training_common.resolve_run_id() == "sfn-execution-abc123"

    def test_falls_back_to_a_fresh_uuid_when_unset(self, monkeypatch):
        monkeypatch.delenv("TRAINING_RUN_ID", raising=False)

        first = training_common.resolve_run_id()
        second = training_common.resolve_run_id()

        # Distinct on every call with no env var -- a manual/local run has
        # no prior attempt to resume, so there's nothing to keep stable.
        assert first != second
        assert first  # non-empty


class TestRunProgress:
    """load_run_progress/save_run_progress/clear_run_progress -- the
    resumable-progress breadcrumb library.ml.backtest.run_backtest reads
    and writes around every candidate, so a task interrupted mid-
    tournament and relaunched with the same run_id skips whatever an
    earlier attempt already settled instead of redoing it."""

    def test_load_returns_none_when_no_breadcrumb_exists(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = False

        assert training_common.load_run_progress(mock_s3, "nfl", "win-probability", "run-1") is None
        mock_s3.get_json.assert_not_called()

    def test_load_returns_the_stored_breadcrumb(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = True
        mock_s3.get_json.return_value = {"evaluated": [{"algorithm": "xgboost"}], "promotions": [], "force_promoted": True}

        progress = training_common.load_run_progress(mock_s3, "nfl", "win-probability", "run-1")

        assert progress["force_promoted"] is True
        mock_s3.get_json.assert_called_once_with("training-runs/nfl/win-probability/run-1/progress.json")

    def test_save_writes_evaluated_promotions_and_force_promoted_flag(self):
        mock_s3 = MagicMock()
        evaluated = [{"algorithm": "xgboost", "score": 0.9, "rank_score": 0.05}]
        promotions = [{"algorithm": "xgboost", "version": 5}]

        training_common.save_run_progress(mock_s3, "nfl", "win-probability", "run-1", evaluated, promotions, True)

        mock_s3.put_json.assert_called_once_with(
            "training-runs/nfl/win-probability/run-1/progress.json",
            {"evaluated": evaluated, "promotions": promotions, "force_promoted": True},
        )

    def test_clear_deletes_the_breadcrumb(self):
        mock_s3 = MagicMock()

        training_common.clear_run_progress(mock_s3, "nfl", "win-probability", "run-1")

        mock_s3.delete_object.assert_called_once_with("training-runs/nfl/win-probability/run-1/progress.json")

    def test_progress_key_is_scoped_by_sport_model_and_run_id_independently(self):
        mock_s3 = MagicMock()

        training_common.clear_run_progress(mock_s3, "ncaafb", "score-margin", "run-2")

        mock_s3.delete_object.assert_called_once_with("training-runs/ncaafb/score-margin/run-2/progress.json")
