"""
Unit tests for library.serving.nfl_reads.list_models -- GET /nfl/models'
per-model card summary (current-version pointer -> model card -> top-5
feature importances -> backtest candidate tournament, when present), one
model name skipped entirely when it has no promoted version. S3 is
mocked. Split out of what used to be one large test_nfl_reads.py -- see
test_nfl_reads_list_events.py's own history note.
"""
from unittest.mock import MagicMock

from library.serving import nfl_reads


class TestListModels:
    def test_returns_a_model_card_summary_per_current_model(self):
        s3 = MagicMock()
        s3.list_keys.return_value = [
            "nfl/win-probability/current.json",
            "nfl/win-probability/v6/model_card.json",
            "nfl/win-probability/v6/model.xgb",
        ]
        s3.object_exists.return_value = True
        s3.get_json.side_effect = [
            {"version": 6},  # current.json pointer
            {
                "model_name": "win-probability", "algorithm": "xgboost", "version": 6,
                "trained_at": "2026-01-01T00:00:00Z", "accuracy": 0.63, "log_loss": 0.65,
                "feature_importances": {"elo_diff": 0.22, "home_rest_days": 0.10},
            },
        ]

        result = nfl_reads.list_models(s3, "nfl")

        assert result["sport"] == "nfl"
        model = result["models"][0]
        assert model["model_name"] == "win-probability"
        assert model["accuracy"] == 0.63
        assert model["top_features"][0] == {"feature": "elo_diff", "importance": 0.22}
        # This card predates the backtesting harness -- no candidates key
        # at all, not an empty list.
        assert model["candidates"] is None
        assert model["candidates_ranked_by"] is None

    def test_includes_the_candidate_tournament_summary_when_present(self):
        s3 = MagicMock()
        s3.list_keys.return_value = ["nfl/win-probability/current.json", "nfl/win-probability/v6/model_card.json"]
        s3.object_exists.return_value = True
        s3.get_json.side_effect = [
            {"version": 6},
            {
                "model_name": "win-probability", "algorithm": "xgboost", "version": 6,
                "trained_at": "2026-01-01T00:00:00Z", "accuracy": 0.63, "log_loss": 0.65,
                "feature_importances": {},
                "candidates_ranked_by": "log_loss",
                "candidates": [
                    {"algorithm": "xgboost", "score": 0.63, "rank_score": 0.65},
                    {"algorithm": "logistic_regression", "score": 0.66, "rank_score": 0.71},
                ],
            },
        ]

        result = nfl_reads.list_models(s3, "nfl")

        model = result["models"][0]
        assert model["candidates_ranked_by"] == "log_loss"
        assert model["candidates"] == [
            {"algorithm": "xgboost", "score": 0.63, "rank_score": 0.65},
            {"algorithm": "logistic_regression", "score": 0.66, "rank_score": 0.71},
        ]

    def test_returns_a_summary_per_model_when_multiple_are_loaded_concurrently(self):
        # Keyed by the exact key string, not an ordered side_effect list --
        # list_models loads each model's chain on its own thread, so call
        # order across models isn't deterministic. An ordered list here
        # would be a real flaky-test risk, not just a style choice.
        cards = {
            "nfl/win-probability/current.json": {"version": 6},
            "nfl/win-probability/v6/model_card.json": {
                "model_name": "win-probability", "algorithm": "xgboost", "version": 6,
                "trained_at": "2026-01-01T00:00:00Z", "accuracy": 0.63, "log_loss": 0.65,
                "feature_importances": {},
            },
            "nfl/score-margin/current.json": {"version": 3},
            "nfl/score-margin/v3/model_card.json": {
                "model_name": "score-margin", "algorithm": "xgboost", "version": 3,
                "trained_at": "2026-01-01T00:00:00Z", "rmse": 9.8, "mae": 7.4,
                "feature_importances": {},
            },
        }
        s3 = MagicMock()
        s3.list_keys.return_value = ["nfl/win-probability/current.json", "nfl/score-margin/current.json"]
        s3.object_exists.return_value = True
        s3.get_json.side_effect = lambda key: cards[key]

        result = nfl_reads.list_models(s3, "nfl")

        assert {m["model_name"] for m in result["models"]} == {"win-probability", "score-margin"}

    def test_skips_a_model_name_with_no_promoted_version(self):
        s3 = MagicMock()
        s3.list_keys.return_value = ["nfl/score-margin/v1/model_card.json"]
        s3.object_exists.return_value = False

        result = nfl_reads.list_models(s3, "nfl")

        assert result["models"] == []
