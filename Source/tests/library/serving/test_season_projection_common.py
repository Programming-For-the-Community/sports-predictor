"""
Unit tests for library.serving.season_projection_common's standings and
feature-row helpers.

Source/tests/library/ is swept by CI jobs that never install the model
stack season_projection_common imports through library.ml.model_types --
importorskip keeps this file from breaking collection there, same as
test_model_loader.py's own guard.
"""
import logging
from unittest.mock import MagicMock

import pytest

for _module in ("joblib", "numpy", "pandas", "sklearn", "xgboost"):
    pytest.importorskip(_module)

from library.serving import season_projection_common  # noqa: E402


def _game(home_score, away_score, event_date="2025-10-05"):
    return {
        "event_date": event_date,
        "participants": [
            {"entity_id": "H", "role": "home", "result": {"score": home_score}},
            {"entity_id": "A", "role": "away", "result": {"score": away_score}},
        ],
    }


class TestRecordGameResultWithTies:
    def _record(self, event, entity_id="H", opponent_id="A", last_dates=None):
        wins, losses, ties, diff, last = {}, {}, {}, {}, dict(last_dates or {})
        season_projection_common.record_game_result_with_ties(event, entity_id, opponent_id, wins, losses, ties, diff, last)
        return wins, losses, ties, diff, last

    def test_credits_a_win(self):
        assert self._record(_game(24, 17)) == ({"H": 1}, {"H": 0}, {"H": 0}, {"H": 7}, {"H": "2025-10-05"})

    def test_credits_a_tie(self):
        wins, losses, ties, diff, _ = self._record(_game(20, 20))
        assert (wins, losses, ties, diff) == ({"H": 0}, {"H": 0}, {"H": 1}, {"H": 0})

    def test_credits_a_loss_from_the_away_side(self):
        wins, losses, _, diff, _ = self._record(_game(24, 17), "A", "H")
        assert (wins, losses, diff) == ({"A": 0}, {"A": 1}, {"A": -7})

    def test_keeps_the_later_last_completed_date(self):
        *_, last = self._record(_game(24, 17, "2025-09-01"), last_dates={"H": "2025-10-01"})
        assert last == {"H": "2025-10-01"}

    def test_skips_a_game_missing_a_score(self):
        assert self._record(_game(None, 17)) == ({}, {}, {}, {}, {})


class TestSeasonWideFeatureRows:
    def test_caches_each_row_and_adds_it_to_its_categorys_stats(self):
        player_team = {"p1": "T1"}
        rows = {"passing": [{"entity_id": "p2", "team_id": "T2"}], "rushing": [{"entity_id": "p1", "team_id": "T9"}]}

        cache, candidates = season_projection_common.season_wide_feature_rows(
            rows, ["passing_yards", "rushing_yards"], {"passing": ["passing_yards"], "rushing": ["rushing_yards"]},
            {"passing_yards": {"p3": 100.0}, "rushing_yards": {}}, player_team,
        )

        assert cache == {"p2": rows["passing"][0], "p1": rows["rushing"][0]}
        assert candidates == {"passing_yards": {"p3", "p2"}, "rushing_yards": {"p1"}}
        assert player_team == {"p1": "T1", "p2": "T2"}


class _NotFound(Exception):
    pass


class TestFillRemainingFeatureRows:
    def test_builds_rows_only_for_candidates_whose_team_has_a_next_event(self):
        cache = {}
        build_row = MagicMock(side_effect=lambda event_key, entity_id: {"event": event_key, "entity_id": entity_id})

        season_projection_common.fill_remaining_feature_rows(
            {"team_next_event": {"T1": "E1"}}, {"p1": "T1", "p2": "T2"}, cache, {"p1", "p2"}, build_row, _NotFound,
            logging.getLogger(),
        )

        assert cache == {"p1": {"event": "E1", "entity_id": "p1"}}
        build_row.assert_called_once_with("E1", "p1")

    def test_leaves_out_candidates_whose_row_fails_to_build(self, caplog):
        cache = {}

        def build_row(event_key, entity_id):
            if entity_id == "missing":
                raise _NotFound()
            raise ValueError("boom")

        season_projection_common.fill_remaining_feature_rows(
            {"team_next_event": {"T1": "E1"}}, {"missing": "T1", "broken": "T1"}, cache, {"missing", "broken"},
            build_row, _NotFound, logging.getLogger("fill-test"),
        )

        assert cache == {}
        assert "Failed to build live features for broken" in caplog.text
        assert "missing" not in caplog.text
