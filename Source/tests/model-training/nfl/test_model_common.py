"""
Unit tests for model_common.py's promotion logic -- get_current_version
and promote_if_better. The rest of model_common.py (load_features,
feature_columns, chronological_split, numeric_frame, evaluate_holdout,
save_model_artifact) is already exercised indirectly through
test_train_model.py and test_train_baseline_model.py's own train()/main()
tests; promotion is new policy logic that deserves direct coverage
rather than only ever running in the shadow of one specific model
script.
"""
from unittest.mock import MagicMock

import model_common


class TestGetCurrentVersion:
    def test_returns_none_when_no_pointer_exists(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = False

        assert model_common.get_current_version(mock_s3, "win-probability") is None

    def test_returns_the_pointed_at_version(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = True
        mock_s3.get_json.return_value = {"version": 4}

        version = model_common.get_current_version(mock_s3, "win-probability")

        assert version == 4
        mock_s3.get_json.assert_called_once_with("nfl/win-probability/current.json")


class TestPromoteIfBetter:
    def test_promotes_directly_when_nothing_is_currently_promoted(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = False

        promoted = model_common.promote_if_better(mock_s3, "win-probability", 1, {"log_loss": 0.65}, "log_loss")

        assert promoted is True
        mock_s3.put_json.assert_called_once_with("nfl/win-probability/current.json", {"version": 1})

    def test_promotes_when_strictly_better_than_current(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = True
        mock_s3.get_json.return_value = {"version": 5, "log_loss": 0.65}

        promoted = model_common.promote_if_better(mock_s3, "win-probability", 6, {"log_loss": 0.60}, "log_loss")

        assert promoted is True
        mock_s3.put_json.assert_called_once_with("nfl/win-probability/current.json", {"version": 6})

    def test_promotes_when_within_tolerance_but_slightly_worse(self):
        # PROMOTION_TOLERANCE is 0.02 -- 1% worse than current should
        # still promote, matching the real run-to-run noise already
        # observed between actual retrains.
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = True
        mock_s3.get_json.return_value = {"version": 5, "log_loss": 0.620}

        promoted = model_common.promote_if_better(mock_s3, "win-probability", 6, {"log_loss": 0.626}, "log_loss")

        assert promoted is True
        mock_s3.put_json.assert_called_once_with("nfl/win-probability/current.json", {"version": 6})

    def test_holds_back_a_meaningful_regression(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = True
        mock_s3.get_json.return_value = {"version": 5, "log_loss": 0.60}

        promoted = model_common.promote_if_better(mock_s3, "win-probability", 6, {"log_loss": 0.70}, "log_loss")

        assert promoted is False
        mock_s3.put_json.assert_not_called()

    def test_reads_the_current_versions_own_model_card_for_comparison(self):
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = True
        mock_s3.get_json.return_value = {"version": 5, "log_loss": 0.62}

        model_common.promote_if_better(mock_s3, "win-probability", 6, {"log_loss": 0.61}, "log_loss")

        mock_s3.get_json.assert_called_with("nfl/win-probability/v5/model_card.json")

    def test_gate_metric_is_parameterized_not_hardcoded_to_log_loss(self):
        # A regression model (player-prop) gates on rmse, not log_loss --
        # promote_if_better must read whichever metric name it's given.
        mock_s3 = MagicMock()
        mock_s3.object_exists.return_value = True
        mock_s3.get_json.return_value = {"version": 1, "rmse": 40.0}

        promoted = model_common.promote_if_better(
            mock_s3, "player-prop-passing-yards", 2, {"rmse": 45.0}, "rmse",
        )

        assert promoted is False
        mock_s3.put_json.assert_not_called()
