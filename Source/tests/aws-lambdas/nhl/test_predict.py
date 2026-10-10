"""
Unit tests for the NHL predict Lambda: event_prediction's leaders panel
and goalies block, live_features' goalie bookkeeping, and the handler's
dispatch. Models, live feature building and AWS resources are mocked.

conftest.py puts aws-lambdas/nhl/predict on sys.path and registers the
handler as nhl_predict.
"""
from unittest.mock import MagicMock, patch

import event_prediction
import live_features
import nhl_predict
from library.serving import event_prediction_common as common
from library.serving import model_loader

EVENT_KEY = "SPORT#NHL#EVENT#401"


def _row(entity_id):
    return {"entity_id": entity_id}


def _predict_by_model(values):
    """model_loader.predict stand-in keyed by the model card's own name."""
    return lambda estimator, card, row: values[card["model_name"]][row["entity_id"]]


def _load(s3, sport, model_name):
    return MagicMock(), {"version": 2, "model_name": model_name}


class TestPredictEventLeaders:
    CANDIDATES = {
        "home": {"skaters": [_row("s1"), _row("s2")], "goalie": _row("g1")},
        "away": {"skaters": [_row("s3")], "goalie": None},
    }
    VALUES = {
        "player-prop-points": {"s1": 0.6, "s2": 1.2, "s3": 0.9},
        "player-prop-goals": {"s1": 0.2, "s2": 0.5, "s3": 0.3},
        "player-prop-assists": {"s1": 0.4, "s2": 0.7, "s3": 0.6},
        "player-prop-shots-total": {"s1": 3.9, "s2": 2.8, "s3": 3.1},
        "player-prop-hits": {"s1": 2.5, "s2": 0.8, "s3": 1.4},
        "player-prop-blocked-shots": {"s1": 0.9, "s2": 0.5, "s3": 1.1},
        "player-prop-saves": {"g1": 27.4},
        "player-prop-goals-against": {"g1": 2.6},
    }

    def _leaders(self, load=_load):
        storage, predictions_table = MagicMock(), MagicMock()
        storage.get_entity.side_effect = lambda sport, entity_id, kind: {"name": f"Name {entity_id}"}
        with patch.object(live_features, "build_live_event_leader_candidates", return_value=self.CANDIDATES), \
             patch.object(common.model_loader, "load_current_model", side_effect=load), \
             patch.object(common.model_loader, "predict", side_effect=_predict_by_model(self.VALUES)):
            return event_prediction.predict_event_leaders(storage, MagicMock(), predictions_table, EVENT_KEY), predictions_table

    def test_each_category_is_sorted_by_its_own_primary_stat(self):
        leaders, _ = self._leaders()

        home = leaders["home"]
        assert [entry["entity_id"] for entry in home["scoring"]] == ["s2", "s1"]
        assert [entry["entity_id"] for entry in home["shooting"]] == ["s1", "s2"]
        assert [entry["entity_id"] for entry in home["physical"]] == ["s1", "s2"]
        assert home["scoring"][0] == {"entity_id": "s2", "name": "Name s2", "goals": 0.5, "assists": 0.7}
        assert home["physical"][0] == {"entity_id": "s1", "name": "Name s1", "hits": 2.5}

    def test_goaltending_is_the_starting_goalie_only_and_empty_without_one(self):
        leaders, _ = self._leaders()

        assert leaders["home"]["goaltending"] == [
            {"entity_id": "g1", "name": "Name g1", "saves": 27.4},
        ]
        assert leaders["away"]["goaltending"] == []

    def test_every_scored_stat_is_recorded_against_the_player(self):
        _, predictions_table = self._leaders()

        keys = {call.args[0]["model_key"] for call in predictions_table.put_item.call_args_list}
        assert "MODEL#player-prop-shots-total#v2#PLAYER#s1" in keys
        assert "MODEL#player-prop-saves#v2#PLAYER#g1" in keys
        assert len(keys) == 3 * 4 + 1

    def test_a_stat_with_no_promoted_model_is_left_out(self):
        def load(s3, sport, model_name):
            if model_name == "player-prop-assists":
                raise model_loader.NoPromotedModelError("none")
            return _load(s3, sport, model_name)

        leaders, _ = self._leaders(load)

        assert "assists" not in leaders["home"]["scoring"][0]
        assert "goals" in leaders["home"]["scoring"][0]

    def test_a_candidate_failure_returns_none_instead_of_raising(self):
        with patch.object(live_features, "build_live_event_leader_candidates", side_effect=RuntimeError("boom")):
            assert event_prediction.predict_event_leaders(MagicMock(), MagicMock(), MagicMock(), EVENT_KEY) is None


class TestPredictEvent:
    def test_result_names_the_goalie_each_side_was_computed_for(self):
        storage = MagicMock()
        storage.get_entity.side_effect = lambda sport, entity_id, kind: {"name": f"Name {entity_id}"}
        row = {
            "home_goalie_id": "g1", "home_goalie_source": "confirmed", "away_goalie_id": None, "away_goalie_source": None,
        }
        with patch.object(live_features.hockey_live, "build_live_event_features", return_value=row), \
             patch.object(common.model_loader, "load_current_model", return_value=(MagicMock(), {"version": 1})), \
             patch.object(common.model_loader, "predict", side_effect=[0.58, 0.4, 3.2, 2.7]), \
             patch.object(event_prediction, "predict_event_leaders", return_value=None):
            result = event_prediction.predict_event(storage, MagicMock(), MagicMock(), "401")

        assert result["event_key"] == EVENT_KEY
        assert result["predictions"]["win_probability"]["home_win_probability"] == 0.58
        assert result["predictions"]["margin"]["value"] == 0.4
        assert result["goalies"] == {"home": {"entity_id": "g1", "source": "confirmed", "name": "Name g1"}}

    def test_live_features_remembers_only_the_latest_event(self):
        rows = {
            "A": {"home_goalie_id": "g1", "home_goalie_source": "probable", "away_goalie_id": "g2", "away_goalie_source": "predicted"},
            "B": {"home_goalie_id": "g3", "home_goalie_source": "confirmed", "away_goalie_id": None, "away_goalie_source": None},
        }
        with patch.object(live_features.hockey_live, "build_live_event_features", side_effect=lambda s, sport, key, events=None: rows[key]):
            live_features.build_live_event_features(MagicMock(), "nhl", "A")
            assert live_features.resolved_goalies("A")["away"] == {"entity_id": "g2", "source": "predicted"}
            live_features.build_live_event_features(MagicMock(), "nhl", "B")

        assert live_features.resolved_goalies("A") == {}
        assert live_features.resolved_goalies("B") == {"home": {"entity_id": "g3", "source": "confirmed"}}


class TestHandler:
    def _invoke(self, event):
        with patch.object(nhl_predict._resources, "all", return_value=("storage", "bucket", "table")):
            return nhl_predict.lambda_handler(event, None)

    def test_snapshot_prediction_takes_the_graded_snapshot(self):
        with patch.object(event_prediction, "snapshot_event", return_value=9) as snapshot:
            result = self._invoke({"detail-type": "SnapshotPrediction", "event_id": "401"})

        assert result == {"status": "ok", "snapshotted": 9}
        snapshot.assert_called_once_with("storage", "bucket", "table", "401")

    def test_cache_miss_routes(self):
        with patch.object(event_prediction, "compute_and_cache_event") as event_route, \
             patch.object(event_prediction, "compute_and_cache_player_prop") as prop_route:
            self._invoke({"detail-type": "ComputeAndCachePrediction", "route": "event", "event_id": "401"})
            self._invoke({
                "detail-type": "ComputeAndCachePrediction", "route": "player_prop", "event_id": "401",
                "entity_id": "s1", "stat": "shots_total",
            })

        event_route.assert_called_once_with("storage", "bucket", "table", "401")
        prop_route.assert_called_once_with("storage", "bucket", "table", "401", "s1", "shots_total")

    def test_scheduled_season_projection_runs_the_projection(self):
        with patch.object(nhl_predict._resources, "storage", return_value="storage"), \
             patch.object(nhl_predict, "_get_storage", return_value="storage"), \
             patch.object(nhl_predict, "_get_model_bucket", return_value="bucket"), \
             patch.object(nhl_predict.season_projection, "run_scheduled", return_value={"status": "ok"}) as run:
            result = self._invoke({"detail-type": "ScheduledSeasonProjection"})

        assert result == {"status": "ok"}
        run.assert_called_once_with("storage", "bucket")

    def test_unrecognized_invocation_is_an_error(self):
        assert self._invoke({"detail-type": "Nope"})["status"] == "error"
