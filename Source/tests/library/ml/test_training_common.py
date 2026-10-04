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
import logging
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from library.ml import training_common


class TestChronologicalSplit:
    def test_splits_by_event_date_by_default(self):
        df = pd.DataFrame({
            "event_date": ["2025-12-03", "2025-12-01", "2025-12-02"],
            "value": [3, 1, 2],
        })

        train_df, test_df = training_common.chronological_split(df, test_fraction=1 / 3)

        assert list(train_df["value"]) == [1, 2]
        assert list(test_df["value"]) == [3]

    def test_date_column_is_configurable_for_a_non_event_centric_dataset(self):
        # NCAA MBB's national-ranking dataset is poll-centric, not
        # event-centric -- no "event_date" column exists at all, only
        # "as_of_date" (see model-training/ncaambb/train_ranking_model.py).
        df = pd.DataFrame({
            "as_of_date": ["2026-03-01", "2026-01-01", "2026-02-01"],
            "value": [3, 1, 2],
        })

        train_df, test_df = training_common.chronological_split(df, test_fraction=1 / 3, date_column="as_of_date")

        assert list(train_df["value"]) == [1, 2]
        assert list(test_df["value"]) == [3]


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


class TestBeats:
    @pytest.mark.parametrize("score, expected", [(0.594, True), (0.595, False), (0.6, False), (0.61, False)])
    def test_log_loss_needs_a_one_percent_improvement(self, score, expected):
        assert training_common.beats(score, 0.6, "log_loss") is expected

    def test_rmse_needs_a_half_percent_improvement(self):
        assert training_common.beats(9.95, 10.0, "rmse")
        assert not training_common.beats(9.96, 10.0, "rmse")

    def test_a_metric_with_no_margin_promotes_on_a_tie(self):
        assert training_common.beats(1.0, 1.0, "mae")


class TestPointerAndCard:
    def test_set_current_version_writes_the_sports_pointer(self):
        mock_s3 = MagicMock()

        training_common.set_current_version(mock_s3, "nba", "win-probability", 9)

        mock_s3.put_json.assert_called_once_with("nba/win-probability/current.json", {"version": 9})

    def test_load_model_card_reads_that_versions_card(self):
        mock_s3 = MagicMock()
        mock_s3.get_json.return_value = {"version": 3}

        assert training_common.load_model_card(mock_s3, "nfl", "home-score", 3) == {"version": 3}
        mock_s3.get_json.assert_called_once_with("nfl/home-score/v3/model_card.json")


class TestUpdatePromotedCandidates:
    """Backfills an already-saved model card's own candidates/
    candidates_ranked_by fields -- see library.ml.backtest.run_backtest's
    own docstring for why the card written at promotion time can be
    incomplete."""

    def test_overwrites_candidates_and_ranked_by_on_the_existing_card(self):
        mock_s3 = MagicMock()
        mock_s3.get_json.return_value = {
            "sport": "nfl", "model_name": "win-probability", "algorithm": "xgboost",
            "version": 7, "log_loss": 0.60, "candidates": [{"algorithm": "xgboost", "rank_score": 0.60}],
        }
        full_candidates = [
            {"algorithm": "xgboost", "rank_score": 0.60},
            {"algorithm": "logistic_regression", "rank_score": 0.65},
        ]

        training_common.update_promoted_candidates(mock_s3, "nfl", "win-probability", 7, full_candidates, "log_loss")

        mock_s3.get_json.assert_called_once_with("nfl/win-probability/v7/model_card.json")
        written_key, written_card = mock_s3.put_json.call_args.args
        assert written_key == "nfl/win-probability/v7/model_card.json"
        assert written_card["candidates"] == full_candidates
        assert written_card["candidates_ranked_by"] == "log_loss"

    def test_leaves_every_other_field_on_the_card_untouched(self):
        mock_s3 = MagicMock()
        mock_s3.get_json.return_value = {
            "algorithm": "xgboost", "version": 7, "log_loss": 0.60,
            "feature_columns": ["a", "b"], "trained_at": "2026-08-15T00:00:00Z",
            "candidates": [{"algorithm": "xgboost", "rank_score": 0.60}],
        }

        training_common.update_promoted_candidates(mock_s3, "nfl", "win-probability", 7, [], "log_loss")

        written_card = mock_s3.put_json.call_args.args[1]
        assert written_card["algorithm"] == "xgboost"
        assert written_card["log_loss"] == 0.60
        assert written_card["feature_columns"] == ["a", "b"]
        assert written_card["trained_at"] == "2026-08-15T00:00:00Z"


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
        mock_s3.get_json.return_value = {"evaluated": [{"algorithm": "xgboost"}], "promotions": []}

        progress = training_common.load_run_progress(mock_s3, "nfl", "win-probability", "run-1")

        assert progress["evaluated"] == [{"algorithm": "xgboost"}]
        mock_s3.get_json.assert_called_once_with("training-runs/nfl/win-probability/run-1/progress.json")

    def test_save_writes_evaluated_and_promotions(self):
        mock_s3 = MagicMock()
        evaluated = [{"algorithm": "xgboost", "score": 0.9, "rank_score": 0.05}]
        promotions = [{"algorithm": "xgboost", "version": 5}]

        training_common.save_run_progress(mock_s3, "nfl", "win-probability", "run-1", evaluated, promotions)

        mock_s3.put_json.assert_called_once_with(
            "training-runs/nfl/win-probability/run-1/progress.json",
            {"evaluated": evaluated, "promotions": promotions},
        )

    def test_clear_deletes_the_breadcrumb(self):
        mock_s3 = MagicMock()

        training_common.clear_run_progress(mock_s3, "nfl", "win-probability", "run-1")

        mock_s3.delete_object.assert_called_once_with("training-runs/nfl/win-probability/run-1/progress.json")

    def test_progress_key_is_scoped_by_sport_model_and_run_id_independently(self):
        mock_s3 = MagicMock()

        training_common.clear_run_progress(mock_s3, "ncaafb", "score-margin", "run-2")

        mock_s3.delete_object.assert_called_once_with("training-runs/ncaafb/score-margin/run-2/progress.json")


class TestLoadLoggedFeatures:
    def test_loads_the_key_and_logs_where_from_and_how_many_rows(self, caplog):
        s3 = MagicMock()
        s3.bucket = "models"
        df = pd.DataFrame({"a": [1, 2, 3]})

        with patch.object(training_common, "load_features", return_value=df) as load,                 caplog.at_level(logging.INFO, logger="test-train"):
            result = training_common.load_logged_features(s3, "nfl/x.parquet", "win-probability", "event", logging.getLogger("test-train"))

        assert result is df
        load.assert_called_once_with(s3, "nfl/x.parquet")
        assert "Loading win-probability training data from s3://models/nfl/x.parquet" in caplog.text
        assert "Loaded 3 event rows" in caplog.text


class TestTrainingScript:
    def _namespace(self):
        return {"train": MagicMock(), "logger": logging.getLogger("test-script")}

    def test_loads_the_dataset_and_trains_on_it(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "models")
        monkeypatch.setenv("AWS_REGION", "us-east-2")
        namespace = self._namespace()
        script = training_common.TrainingScript(namespace, features_key="k", row_noun="event", model_name="win-probability")

        with patch.object(training_common, "S3Manager") as s3_cls,                 patch.object(training_common, "load_logged_features", return_value="df") as load:
            script.main()

        s3_cls.assert_called_once_with("models", region="us-east-2")
        load.assert_called_once_with(s3_cls.return_value, "k", "win-probability", "event", namespace["logger"])
        namespace["train"].assert_called_once_with(s3_cls.return_value, "df")

    def test_a_per_run_target_names_the_model_and_is_passed_to_train(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "models")
        namespace = self._namespace()
        script = training_common.TrainingScript(
            namespace, features_key="k", row_noun="event", target=lambda: "margin", model_name_for=lambda t: f"score-{t}",
        )

        with patch.object(training_common, "S3Manager") as s3_cls,                 patch.object(training_common, "load_logged_features", return_value="df") as load:
            script.main()

        assert load.call_args.args[2] == "score-margin"
        namespace["train"].assert_called_once_with(s3_cls.return_value, "df", "margin")


class TestModelJob:
    def test_prepares_then_trains_with_the_scripts_config(self):
        trainer = MagicMock(return_value={"promotions": []})
        namespace = {"logger": logging.getLogger("test-job")}
        job = training_common.ModelJob(
            namespace, trainer=trainer, sport="f1", model_name="podium-probability", features_key="k", row_noun="driver-race",
            label_column="label_podium", non_feature_columns={"event_key"}, candidates=["c"],
            prepare=lambda df: df[df["label_podium"].notna()], drop_null_label=True,
        )
        df = pd.DataFrame({"event_key": ["a", "b"], "x": [1, 2], "label_podium": [1.0, None]})

        assert job.train("s3", df) == {"promotions": []}
        assert job.feature_columns(df) == ["x"]
        prepared = trainer.call_args.args[1]
        assert list(prepared["event_key"]) == ["a"]
        assert trainer.call_args.args[2:] == ("f1", "podium-probability")
        assert trainer.call_args.kwargs == {
            "label_column": "label_podium", "non_feature_columns": {"event_key"}, "candidates": ["c"],
            "logger": namespace["logger"], "drop_null_label": True,
        }

    def test_main_runs_the_training_script(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "models")
        namespace = {"logger": logging.getLogger("test-job"), "train": MagicMock()}
        job = training_common.ModelJob(
            namespace, trainer=MagicMock(), sport="f1", model_name="m", features_key="k", row_noun="row",
            label_column="l", non_feature_columns=set(), candidates=[],
        )

        with patch.object(training_common, "S3Manager"),                 patch.object(training_common, "load_logged_features", return_value="df"):
            job.main()

        namespace["train"].assert_called_once()
