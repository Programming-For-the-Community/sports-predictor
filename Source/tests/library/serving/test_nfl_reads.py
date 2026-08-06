"""
Unit tests for library/serving/nfl_reads.py -- moved from
Source/tests/aws-lambdas/nfl/test_predict.py's TestListEvents/
TestListModels/TestRoundLabel now that this logic is shared between the
heavy predict Lambda and the light predict-read Lambda instead of living
inside predict/handler.py alone. Storage/S3/predictions-table objects are
mocked and passed in directly -- this module owns no Lambda-lifecycle
state of its own.
"""
from unittest.mock import MagicMock

from library.serving import nfl_reads


def _completed_event(event_key, season, home_id, away_id, home_score, away_score, *,
                      event_id=None, event_date="2025-09-14", season_type=2, week=1):
    return {
        "event_key": event_key, "event_id": event_id or event_key, "event_date": event_date,
        "season": season, "season_type": season_type, "week": week, "status": "completed",
        "participants": [
            {"entity_id": home_id, "role": "home", "result": {"score": home_score, "won": home_score > away_score}},
            {"entity_id": away_id, "role": "away", "result": {"score": away_score, "won": away_score > home_score}},
        ],
    }


def _scheduled_event(event_key, season, event_date, home_id, away_id, *,
                      event_id=None, season_type=2, week=1):
    return {
        "event_key": event_key, "event_id": event_id or event_key, "event_date": event_date,
        "season": season, "season_type": season_type, "week": week, "status": "scheduled",
        "participants": [
            {"entity_id": home_id, "role": "home", "result": None},
            {"entity_id": away_id, "role": "away", "result": None},
        ],
    }


def _prediction_row(model_key, predicted_value):
    return {"model_key": model_key, "predicted_value": predicted_value}


class TestRoundLabel:
    def test_regular_season_is_none(self):
        assert nfl_reads._round_label({"season_type": 2, "week": 5}) is None

    def test_wild_card_is_week_1(self):
        assert nfl_reads._round_label({"season_type": 3, "week": 1}) == "Wild Card"

    def test_divisional_is_week_2(self):
        assert nfl_reads._round_label({"season_type": 3, "week": 2}) == "Divisional"

    def test_conference_championship_is_week_3(self):
        assert nfl_reads._round_label({"season_type": 3, "week": 3}) == "Conference Championship"

    def test_super_bowl_is_week_5(self):
        assert nfl_reads._round_label({"season_type": 3, "week": 5}) == "Super Bowl"

    def test_week_4_pro_bowl_has_no_label(self):
        # Always the Pro Bowl -- already excluded by is_real_franchise_matchup
        # before _round_label is ever consulted, so this documents "unmapped",
        # not an expected real code path.
        assert nfl_reads._round_label({"season_type": 3, "week": 4}) is None


class TestListEvents:
    def test_returns_events_for_the_requested_status(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [
            {
                "event_id": "401547417", "event_date": "2025-09-28", "status": "scheduled",
                "season": 2025, "season_type": 2, "week": 4,
                "participants": [{"entity_id": "12", "role": "home"}, {"entity_id": "24", "role": "away"}],
            },
        ]

        result = nfl_reads.list_events(storage, MagicMock(), "nfl", "scheduled")

        assert result["sport"] == "nfl"
        assert result["events"][0]["event_id"] == "401547417"
        storage.get_all_events.assert_called_once_with("nfl", status="scheduled")

    def test_completed_status_scopes_to_the_most_recent_week_only(self):
        storage = MagicMock()
        predictions_table = MagicMock()
        predictions_table.query.return_value = []
        storage.get_all_events.return_value = [
            _completed_event("EVT#1", 2025, "12", "13", 24, 17, event_date="2025-09-07", week=1),
            _completed_event("EVT#2", 2025, "12", "13", 20, 10, event_date="2025-09-14", week=2),
            _completed_event("EVT#3", 2025, "12", "13", 30, 27, event_date="2025-09-15", week=2),
        ]

        result = nfl_reads.list_events(storage, predictions_table, "nfl", "completed")

        assert [e["event_id"] for e in result["events"]] == ["EVT#2", "EVT#3"]

    def test_scheduled_status_scopes_to_the_soonest_week_only(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [
            _scheduled_event("EVT#1", 2025, "2025-09-21", "12", "13", week=3),
            _scheduled_event("EVT#2", 2025, "2025-09-14", "12", "13", week=2),
            _scheduled_event("EVT#3", 2025, "2025-09-14", "1", "2", week=2),
        ]

        result = nfl_reads.list_events(storage, MagicMock(), "nfl", "scheduled")

        assert [e["event_id"] for e in result["events"]] == ["EVT#2", "EVT#3"]

    def test_scheduled_status_is_empty_when_the_next_week_has_not_been_ingested_yet(self):
        storage = MagicMock()
        storage.get_all_events.return_value = []

        result = nfl_reads.list_events(storage, MagicMock(), "nfl", "scheduled")

        assert result["events"] == []

    def test_completed_events_include_prediction_comparison_when_one_was_logged(self):
        storage = MagicMock()
        predictions_table = MagicMock()
        predictions_table.query.return_value = [
            _prediction_row("MODEL#win-probability#v6", {"home_win_probability": 0.71, "model_version": 6}),
            _prediction_row("MODEL#score-margin#v3", {"value": 6.2, "model_version": 3}),
            _prediction_row("MODEL#home-score#v2", {"value": 27.4, "model_version": 2}),
            _prediction_row("MODEL#away-score#v2", {"value": 21.2, "model_version": 2}),
        ]
        storage.get_all_events.return_value = [_completed_event("EVT#1", 2025, "12", "13", 24, 17)]

        result = nfl_reads.list_events(storage, predictions_table, "nfl", "completed")

        comparison = result["events"][0]["prediction_comparison"]
        assert comparison["predicted_home_win_probability"] == 0.71
        assert comparison["predicted_home_won"] is True
        assert comparison["actual_home_won"] is True
        assert comparison["correct"] is True
        assert comparison["actual_margin"] == 7
        assert comparison["actual_home_score"] == 24
        assert comparison["actual_away_score"] == 17

    def test_excludes_the_pro_bowl_from_the_list(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [
            _completed_event("EVT#REAL", 2025, "12", "13", 24, 17, event_date="2025-09-14", week=2),
            # AFC (31) vs NFC (32) -- the Pro Bowl, same week as the real game.
            _completed_event("EVT#PROBOWL", 2025, "31", "32", 40, 35, event_date="2025-09-14", week=2),
        ]
        predictions_table = MagicMock()
        predictions_table.query.return_value = []

        result = nfl_reads.list_events(storage, predictions_table, "nfl", "completed")

        assert [e["event_id"] for e in result["events"]] == ["EVT#REAL"]

    def test_postseason_events_carry_a_round_label(self):
        storage = MagicMock()
        predictions_table = MagicMock()
        predictions_table.query.return_value = []
        storage.get_all_events.return_value = [
            _completed_event("EVT#WC", 2025, "12", "13", 24, 17, event_date="2026-01-11", season_type=3, week=1),
        ]

        result = nfl_reads.list_events(storage, predictions_table, "nfl", "completed")

        assert result["events"][0]["round"] == "Wild Card"

    def test_regular_season_events_have_no_round_label(self):
        storage = MagicMock()
        predictions_table = MagicMock()
        predictions_table.query.return_value = []
        storage.get_all_events.return_value = [
            _completed_event("EVT#1", 2025, "12", "13", 24, 17, season_type=2, week=4),
        ]

        result = nfl_reads.list_events(storage, predictions_table, "nfl", "completed")

        assert result["events"][0]["round"] is None

    def test_completed_events_have_no_comparison_when_nothing_was_ever_predicted(self):
        storage = MagicMock()
        predictions_table = MagicMock()
        predictions_table.query.return_value = []
        storage.get_all_events.return_value = [_completed_event("EVT#1", 2025, "12", "13", 24, 17)]

        result = nfl_reads.list_events(storage, predictions_table, "nfl", "completed")

        assert result["events"][0]["prediction_comparison"] is None


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


class TestGetSeasonProjection:
    def test_returns_the_cached_projection_from_its_own_key(self):
        s3 = MagicMock()
        s3.object_exists.return_value = True
        s3.get_json.return_value = {"sport": "nfl", "season": 2025, "standings": []}

        result = nfl_reads.get_season_projection(s3, "nfl")

        assert result == {"sport": "nfl", "season": 2025, "standings": []}
        s3.get_json.assert_called_once_with("season-projections/nfl/latest.json")

    def test_returns_none_when_the_scheduled_job_hasnt_written_one_yet(self):
        s3 = MagicMock()
        s3.object_exists.return_value = False

        result = nfl_reads.get_season_projection(s3, "nfl")

        assert result is None
        s3.get_json.assert_not_called()
