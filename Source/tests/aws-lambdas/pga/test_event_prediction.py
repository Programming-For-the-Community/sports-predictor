"""
Unit tests for pga/predict/event_prediction.py. live_features/model_loader
are mocked at the module boundary; storage/s3/predictions_table are plain
MagicMocks.
"""
from unittest.mock import MagicMock, patch

import pytest

import event_prediction
import live_features
import model_loader
from library.serving import pga_reads


def _field_event(event_id="999", status="scheduled", result=None, par=70):
    default_result = {
        "finish_position": 3, "score_to_par": -10, "total_strokes": 278.0, "status": "finished",
        "rounds": [{"round": 1, "score_to_par": -4, "total_strokes": 68.0}],
    }
    return {
        "event_key": f"SPORT#PGA#EVENT#{event_id}", "event_id": event_id, "event_type": "field",
        "status": status, "tournament_name": "BMW Championship", "par": par,
        "participants": [{"entity_id": "1", "result": default_result if result is None else result}],
    }


def _built_field_features(status="scheduled", result=None, par=70):
    return {
        "event": _field_event(status=status, result=result, par=par),
        "golfer_rows": {"1": {"golfer": {"f": 1}, "rounds": {2: {"f": 2}}}},
        "cutline_row": {"f": 3},
    }


class TestScore:
    def test_returns_none_when_no_model_promoted(self):
        model_cache = {}
        s3, predictions_table = MagicMock(), MagicMock()
        with patch.object(event_prediction.model_loader, "load_current_model", side_effect=model_loader.NoPromotedModelError()):
            result = event_prediction._score(model_cache, s3, predictions_table, "EK", "top-10-probability", {}, "SUFFIX")

        assert result is None
        predictions_table.put_item.assert_not_called()

    def test_records_and_returns_the_scored_value(self):
        model_cache = {}
        s3, predictions_table = MagicMock(), MagicMock()
        with patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 2})), \
             patch.object(event_prediction.model_loader, "predict", return_value=0.42):
            result = event_prediction._score(model_cache, s3, predictions_table, "EK", "top-10-probability", {}, "SUFFIX")

        assert result == {"value": 0.42, "model_version": 2}
        predictions_table.put_item.assert_called_once()


class TestPredictFieldEvent:
    def test_scores_every_model_for_every_golfer(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        storage.get_entity.return_value = {"name": "Scottie Scheffler", "metadata": {"country": "USA"}}
        predictions_table.query.return_value = []  # no historical round predictions to backfill
        with patch.object(event_prediction.live_features, "build_live_field_features", return_value=_built_field_features()), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 1})), \
             patch.object(event_prediction.model_loader, "predict", return_value=0.5):
            result = event_prediction.predict_field_event(storage, s3, predictions_table, "999")

        golfer = result["field"][0]
        assert golfer["entity_id"] == "1"
        assert golfer["name"] == "Scottie Scheffler"
        assert set(golfer["predictions"]) == {"top_10_probability", "top_5_probability", "projected_score_to_par", "rounds"}
        assert golfer["predictions"]["rounds"]["round_2"]["value"] == 0.5
        assert result["par"] == 70
        assert result["cutline"]["projected_cut_score"]["value"] == 0.5

    def test_a_model_with_no_promoted_version_is_simply_omitted(self):
        """No model is more important than another -- missing top-5
        shouldn't block top-10/score/cutline from still being returned."""
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        storage.get_entity.return_value = None
        predictions_table.query.return_value = []  # no historical round predictions to backfill

        def _load(s3_arg, sport, model_name):
            if model_name == "top-5-probability":
                raise model_loader.NoPromotedModelError()
            return (MagicMock(), {"version": 1})

        with patch.object(event_prediction.live_features, "build_live_field_features", return_value=_built_field_features()), \
             patch.object(event_prediction.model_loader, "load_current_model", side_effect=_load), \
             patch.object(event_prediction.model_loader, "predict", return_value=0.5):
            result = event_prediction.predict_field_event(storage, s3, predictions_table, "999")

        predictions = result["field"][0]["predictions"]
        assert "top_5_probability" not in predictions
        assert "top_10_probability" in predictions

    def test_no_actual_block_when_the_golfer_has_no_result_data_at_all(self):
        # Not status-gated -- a golfer genuinely hasn't played yet,
        # regardless of what the tournament's own overall status says.
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        storage.get_entity.return_value = None
        no_result = {"finish_position": None, "score_to_par": None, "rounds": []}
        with patch.object(event_prediction.live_features, "build_live_field_features", return_value=_built_field_features(status="scheduled", result=no_result)), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 1})), \
             patch.object(event_prediction.model_loader, "predict", return_value=0.5):
            result = event_prediction.predict_field_event(storage, s3, predictions_table, "999")

        assert "actual" not in result["field"][0]

    def test_actual_block_present_mid_tournament_not_gated_on_completed_status(self):
        # The real bug this fixes: a genuine current standing (mid-
        # tournament, status still "scheduled") was previously invisible
        # in the response until the whole event finished.
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        storage.get_entity.return_value = None
        predictions_table.query.return_value = []  # no historical round predictions to backfill
        with patch.object(event_prediction.live_features, "build_live_field_features", return_value=_built_field_features(status="scheduled")), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 1})), \
             patch.object(event_prediction.model_loader, "predict", return_value=0.5):
            result = event_prediction.predict_field_event(storage, s3, predictions_table, "999")

        assert result["field"][0]["actual"] == {
            "finish_position": 3, "score_to_par": -10, "total_strokes": 278.0, "status": "finished",
            "rounds": [{"round": 1, "score_to_par": -4, "total_strokes": 68.0}], "thru": None,
        }

    def test_actual_status_is_this_golfers_own_real_status_not_inferred_from_having_a_standing(self):
        # The real bug: a golfer with a current standing mid-tournament is
        # NOT necessarily "finished" -- their own real ESPN status (still
        # out on the course) must come through unchanged.
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        storage.get_entity.return_value = None
        predictions_table.query.return_value = []  # no historical round predictions to backfill
        in_progress = {
            "finish_position": 5, "score_to_par": -3, "status": "in_progress",
            "rounds": [{"round": 1, "score_to_par": -3, "total_strokes": 69.0}],
        }
        with patch.object(event_prediction.live_features, "build_live_field_features", return_value=_built_field_features(status="scheduled", result=in_progress)), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 1})), \
             patch.object(event_prediction.model_loader, "predict", return_value=0.5):
            result = event_prediction.predict_field_event(storage, s3, predictions_table, "999")

        assert result["field"][0]["actual"]["status"] == "in_progress"

    def test_actual_block_present_for_a_completed_event(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        storage.get_entity.return_value = None
        predictions_table.query.return_value = []  # no historical round predictions to backfill
        with patch.object(event_prediction.live_features, "build_live_field_features", return_value=_built_field_features(status="completed")), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 1})), \
             patch.object(event_prediction.model_loader, "predict", return_value=0.5):
            result = event_prediction.predict_field_event(storage, s3, predictions_table, "999")

        assert result["field"][0]["actual"]["finish_position"] == 3

    def test_actual_rounds_present_even_when_finish_position_and_score_to_par_are_still_none(self):
        # A completed round's own result can land a normalize cycle
        # before the tournament-level summary refreshes.
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        storage.get_entity.return_value = None
        predictions_table.query.return_value = []  # no historical round predictions to backfill
        rounds_only = {"finish_position": None, "score_to_par": None, "rounds": [{"round": 1, "score_to_par": -4, "total_strokes": 68.0}]}
        with patch.object(event_prediction.live_features, "build_live_field_features", return_value=_built_field_features(status="scheduled", result=rounds_only)), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 1})), \
             patch.object(event_prediction.model_loader, "predict", return_value=0.5):
            result = event_prediction.predict_field_event(storage, s3, predictions_table, "999")

        assert result["field"][0]["actual"]["rounds"] == [{"round": 1, "score_to_par": -4, "total_strokes": 68.0}]

    def test_actual_block_includes_the_real_cumulative_total_strokes(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        storage.get_entity.return_value = None
        predictions_table.query.return_value = []
        with patch.object(event_prediction.live_features, "build_live_field_features", return_value=_built_field_features(status="scheduled")), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 1})), \
             patch.object(event_prediction.model_loader, "predict", return_value=0.5):
            result = event_prediction.predict_field_event(storage, s3, predictions_table, "999")

        assert result["field"][0]["actual"]["total_strokes"] == 278.0

    def test_actual_block_includes_thru_for_an_in_progress_golfer(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        storage.get_entity.return_value = None
        predictions_table.query.return_value = []
        in_progress = {
            "finish_position": 5, "score_to_par": -3, "status": "in_progress", "thru": 14,
            "rounds": [{"round": 2, "score_to_par": -1, "total_strokes": 69.0}],
        }
        with patch.object(event_prediction.live_features, "build_live_field_features", return_value=_built_field_features(status="scheduled", result=in_progress)), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 1})), \
             patch.object(event_prediction.model_loader, "predict", return_value=0.5):
            result = event_prediction.predict_field_event(storage, s3, predictions_table, "999")

        assert result["field"][0]["actual"]["thru"] == 14

    def test_par_is_none_when_the_stored_event_has_no_par_value(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        storage.get_entity.return_value = None
        predictions_table.query.return_value = []
        with patch.object(event_prediction.live_features, "build_live_field_features", return_value=_built_field_features(par=None)), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 1})), \
             patch.object(event_prediction.model_loader, "predict", return_value=0.5):
            result = event_prediction.predict_field_event(storage, s3, predictions_table, "999")

        assert result["par"] is None

    def test_backfills_a_played_rounds_own_historical_pre_round_forecast(self):
        # default_result has round 1 already played -- its own original
        # forecast (recorded before round 1 started, never re-scored once
        # played) should be recovered from predictions_table and merged
        # in next to round 2's freshly-scored (live_features golfer_rows
        # only has round 2 in this fixture) projection.
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        storage.get_entity.return_value = None
        predictions_table.query.return_value = [
            {"event_key": "SPORT#PGA#EVENT#999", "model_key": "MODEL#round-1#v2#GOLFER#1", "predicted_value": {"value": -1.5}},
            {"event_key": "SPORT#PGA#EVENT#999", "model_key": "MODEL#top-10-probability#v1#GOLFER#1", "predicted_value": {"value": 0.3}},
        ]
        with patch.object(event_prediction.live_features, "build_live_field_features", return_value=_built_field_features(status="scheduled")), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 1})), \
             patch.object(event_prediction.model_loader, "predict", return_value=0.5):
            result = event_prediction.predict_field_event(storage, s3, predictions_table, "999")

        rounds = result["field"][0]["predictions"]["rounds"]
        assert rounds["round_1"] == {"value": -1.5, "model_version": 2}
        assert rounds["round_2"] == {"value": 0.5, "model_version": 1}  # freshly scored, untouched by the backfill

    def test_skips_the_historical_query_entirely_when_no_round_has_been_played_yet(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        storage.get_entity.return_value = None
        no_result = {"finish_position": None, "score_to_par": None, "rounds": []}
        with patch.object(event_prediction.live_features, "build_live_field_features", return_value=_built_field_features(status="scheduled", result=no_result)), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 1})), \
             patch.object(event_prediction.model_loader, "predict", return_value=0.5):
            event_prediction.predict_field_event(storage, s3, predictions_table, "999")

        predictions_table.query.assert_not_called()


class TestHistoricalRoundPredictions:
    def test_parses_round_and_golfer_out_of_the_model_key_ignoring_non_round_rows(self):
        predictions_table = MagicMock()
        predictions_table.query.return_value = [
            {"model_key": "MODEL#round-3#v4#GOLFER#1085", "predicted_value": {"value": -2.0}},
            {"model_key": "MODEL#round-1#v4#GOLFER#2001", "predicted_value": {"value": 1.0}},
            {"model_key": "MODEL#top-10-probability#v4#GOLFER#1085", "predicted_value": {"value": 0.4}},
            {"model_key": "MODEL#cutline#v2#CUTLINE", "predicted_value": {"value": -3.0}},
        ]

        result = event_prediction._historical_round_predictions(predictions_table, "SPORT#PGA#EVENT#999")

        assert result == {
            "1085": {3: {"value": -2.0, "model_version": 4}},
            "2001": {1: {"value": 1.0, "model_version": 4}},
        }

    def test_empty_when_nothing_recorded(self):
        predictions_table = MagicMock()
        predictions_table.query.return_value = []

        assert event_prediction._historical_round_predictions(predictions_table, "SPORT#PGA#EVENT#999") == {}


class TestFieldSortKey:
    def test_sorts_ascending_by_projected_score(self):
        entries = [
            {"predictions": {"projected_score_to_par": {"value": -2.0}}},
            {"predictions": {"projected_score_to_par": {"value": -10.0}}},
        ]
        entries.sort(key=event_prediction._field_sort_key)
        assert [e["predictions"]["projected_score_to_par"]["value"] for e in entries] == [-10.0, -2.0]

    def test_falls_back_to_top_10_probability_descending_when_score_missing(self):
        entries = [
            {"predictions": {"top_10_probability": {"value": 0.3}}},
            {"predictions": {"top_10_probability": {"value": 0.8}}},
        ]
        entries.sort(key=event_prediction._field_sort_key)
        assert [e["predictions"]["top_10_probability"]["value"] for e in entries] == [0.8, 0.3]

    def test_a_model_with_score_always_sorts_before_one_without(self):
        entries = [
            {"predictions": {"top_10_probability": {"value": 0.99}}},
            {"predictions": {"projected_score_to_par": {"value": 5.0}}},
        ]
        entries.sort(key=event_prediction._field_sort_key)
        assert "projected_score_to_par" in entries[0]["predictions"]


class TestPredictMatchEvent:
    def _match_event(self, status="scheduled"):
        return {
            "event_key": "SPORT#PGA#EVENT#999", "event_id": "999", "event_type": "match_play", "status": status,
            "match_format": "Foursomes", "session_name": "Thursday Foursomes",
            "participants": [
                {"entity_id": "1", "role": "home", "golfer_entity_ids": ["1085", "1086"], "result": {"won": True, "halved": False}},
                {"entity_id": "3", "role": "away", "golfer_entity_ids": ["2001"], "result": {"won": False, "halved": False}},
            ],
        }

    def test_scores_the_match_win_probability_model(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        storage.get_entity.return_value = {"name": "USA", "metadata": {}}
        built = {"event": self._match_event(), "features": {"f": 1}}
        with patch.object(event_prediction.live_features, "build_live_match_features", return_value=built), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 3})), \
             patch.object(event_prediction.model_loader, "predict", return_value=0.57):
            result = event_prediction.predict_match_event(storage, s3, predictions_table, "999")

        assert result["predictions"]["match_win_probability"] == {"value": 0.57, "model_version": 3}
        assert result["home"]["entity_id"] == "1"
        assert result["away"]["entity_id"] == "3"

    def test_actual_block_present_only_when_completed(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        storage.get_entity.return_value = None
        built = {"event": self._match_event(status="completed"), "features": {"f": 1}}
        with patch.object(event_prediction.live_features, "build_live_match_features", return_value=built), \
             patch.object(event_prediction.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 1})), \
             patch.object(event_prediction.model_loader, "predict", return_value=0.5):
            result = event_prediction.predict_match_event(storage, s3, predictions_table, "999")

        assert result["actual"] == {"home_won": True, "halved": False}


class TestPredictEvent:
    def test_raises_event_not_found(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        storage.get_event.return_value = None

        with pytest.raises(live_features.EventNotFoundError):
            event_prediction.predict_event(storage, s3, predictions_table, "999")

    def test_dispatches_field_events_to_predict_field_event(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        storage.get_event.return_value = _field_event()
        with patch.object(event_prediction, "predict_field_event", return_value={"ok": "field"}) as mock_predict:
            result = event_prediction.predict_event(storage, s3, predictions_table, "999")

        mock_predict.assert_called_once()
        assert result == {"ok": "field"}

    def test_dispatches_match_play_events_to_predict_match_event(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        storage.get_event.return_value = {"event_type": "match_play"}
        with patch.object(event_prediction, "predict_match_event", return_value={"ok": "match"}) as mock_predict:
            result = event_prediction.predict_event(storage, s3, predictions_table, "999")

        mock_predict.assert_called_once()
        assert result == {"ok": "match"}

    def test_dispatches_cup_events_to_predict_cup_event(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        storage.get_event.return_value = {"event_type": "cup"}
        with patch.object(event_prediction, "predict_cup_event", return_value={"ok": "cup"}) as mock_predict:
            result = event_prediction.predict_event(storage, s3, predictions_table, "999")

        mock_predict.assert_called_once()
        assert result == {"ok": "cup"}

    def test_raises_malformed_for_an_unrecognized_event_type(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        storage.get_event.return_value = {"event_type": "something_new"}

        with pytest.raises(live_features.MalformedEventError):
            event_prediction.predict_event(storage, s3, predictions_table, "999")


class TestComputeAndCacheEvent:
    def test_caches_a_successful_prediction_with_the_matching_model_versions_map(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        result = {"event_type": "field", "status": "scheduled"}
        with patch.object(event_prediction, "predict_event", return_value=result), \
             patch.object(event_prediction.prediction_cache, "current_model_versions", return_value={"v": 1}) as mock_versions, \
             patch.object(event_prediction.prediction_cache, "put_cached") as mock_put, \
             patch.object(event_prediction.prediction_cache, "clear_in_progress") as mock_clear:
            event_prediction.compute_and_cache_event(storage, s3, predictions_table, "999")

        mock_versions.assert_called_once_with(s3, "pga", pga_reads.FIELD_EVENT_MODEL_VERSIONS)
        mock_put.assert_called_once()
        mock_clear.assert_called_once()

    def test_records_a_real_rounds_fingerprint_for_a_field_event(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        storage.get_event.return_value = {
            "event_type": "field",
            "participants": [{"entity_id": "1", "result": {"rounds": [{"round": 1}, {"round": 2}]}}],
        }
        result = {"event_type": "field", "status": "scheduled"}
        with patch.object(event_prediction, "predict_event", return_value=result), \
             patch.object(event_prediction.prediction_cache, "current_model_versions", return_value={"v": 1}), \
             patch.object(event_prediction.prediction_cache, "put_cached") as mock_put, \
             patch.object(event_prediction.prediction_cache, "clear_in_progress"):
            event_prediction.compute_and_cache_event(storage, s3, predictions_table, "999")

        assert mock_put.call_args.args[-1] == 2  # extra_fingerprint is the last positional arg

    def test_cup_events_use_the_cup_model_versions_map(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        result = {"event_type": "cup", "status": "scheduled"}
        with patch.object(event_prediction, "predict_event", return_value=result), \
             patch.object(event_prediction.prediction_cache, "current_model_versions", return_value={}) as mock_versions, \
             patch.object(event_prediction.prediction_cache, "put_cached"), \
             patch.object(event_prediction.prediction_cache, "clear_in_progress"):
            event_prediction.compute_and_cache_event(storage, s3, predictions_table, "999")

        mock_versions.assert_called_once_with(s3, "pga", pga_reads.CUP_MODEL_VERSIONS)

    def test_a_recognized_error_is_cached_as_a_negative_entry(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        with patch.object(event_prediction, "predict_event", side_effect=live_features.EventNotFoundError("no such event")), \
             patch.object(event_prediction.prediction_cache, "put_error_cached") as mock_put_error, \
             patch.object(event_prediction.prediction_cache, "clear_in_progress") as mock_clear:
            event_prediction.compute_and_cache_event(storage, s3, predictions_table, "999")

        mock_put_error.assert_called_once()
        assert mock_put_error.call_args.args[2] == "EventNotFoundError"
        mock_clear.assert_called_once()

    def test_an_unrecognized_exception_propagates_but_still_clears_in_progress(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        with patch.object(event_prediction, "predict_event", side_effect=RuntimeError("boom")), \
             patch.object(event_prediction.prediction_cache, "clear_in_progress") as mock_clear:
            with pytest.raises(RuntimeError):
                event_prediction.compute_and_cache_event(storage, s3, predictions_table, "999")

        mock_clear.assert_called_once()
