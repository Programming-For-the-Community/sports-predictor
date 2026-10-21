"""
Unit tests for library.serving.bracket_projection -- the real postseason
lookups every head-to-head sport's bracket simulation starts from.
"""
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

from library.serving import bracket_projection


def _event(home: str, away: str, status: str = "scheduled", **extra) -> dict:
    return {
        "event_key": f"EVENT#{home}-{away}", "event_id": f"{home}-{away}", "status": status,
        "participants": [{"entity_id": home, "role": "home"}, {"entity_id": away, "role": "away"}],
        **extra,
    }


def _logged_row(home_win_probability: float) -> dict:
    return {
        "model_key": "MODEL#win-probability#v1", "generated_at": "2026-01-01T00:00:00+00:00",
        "predicted_value": {"home_win_probability": home_win_probability},
    }


def _lookup(predictions_table, compute=None):
    return bracket_projection.logged_home_probability(
        SimpleNamespace(compute_and_cache_event=compute or MagicMock()), MagicMock(), MagicMock(), predictions_table,
        logging.getLogger("test-bracket"),
    )


class TestRealPostseasonEvents:
    def test_keeps_only_this_seasons_postseason_games_of_either_status(self):
        storage = MagicMock()
        scheduled = _event("a", "b", season=2026, playoff=True)
        completed = _event("g", "h", "completed", season=2026, playoff=True)
        storage.get_all_events.side_effect = lambda sport, status: {
            "scheduled": [scheduled, _event("c", "d", season=2026, playoff=False)],
            "completed": [completed, _event("e", "f", "completed", season=2025, playoff=True)],
        }[status]

        result = bracket_projection.real_postseason_events(storage, "nfl", 2026, lambda e: e["playoff"])

        assert result == [scheduled, completed]


class TestLoggedHomeProbability:
    def test_uses_the_logged_prediction_without_recomputing(self):
        table = MagicMock()
        table.query.return_value = [_logged_row(0.35)]
        compute = MagicMock()

        assert _lookup(table, compute)(_event("a", "b")) == 0.35
        compute.assert_not_called()

    def test_computes_one_for_an_unviewed_scheduled_game(self):
        table = MagicMock()
        table.query.side_effect = [[], [_logged_row(0.6)]]
        compute = MagicMock()

        assert _lookup(table, compute)(_event("a", "b")) == 0.6
        compute.assert_called_once()

    def test_never_computes_one_for_a_finished_game(self):
        table = MagicMock()
        table.query.return_value = []
        compute = MagicMock()

        assert _lookup(table, compute)(_event("a", "b", "completed")) is None
        compute.assert_not_called()

    def test_a_failed_compute_is_none_not_an_error(self, caplog):
        table = MagicMock()
        table.query.return_value = []

        assert _lookup(table, MagicMock(side_effect=RuntimeError("boom")))(_event("a", "b")) is None
        assert "Failed computing a live prediction" in caplog.text


class TestPostseasonActive:
    TODAY = __import__("datetime").date(2026, 5, 10)

    def _storage(self, scheduled=(), completed=()):
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status, **kwargs: list(scheduled if status == "scheduled" else completed)
        return storage

    def test_a_scheduled_postseason_game_means_active(self):
        storage = self._storage(scheduled=[_event("a", "b", playoff=True)])
        assert bracket_projection.postseason_active(storage, "nba", lambda e: e["playoff"], self.TODAY)

    def test_a_postseason_game_finished_in_the_last_two_days_means_active(self):
        storage = self._storage(completed=[_event("a", "b", "completed", playoff=True, event_date="2026-05-08")])
        assert bracket_projection.postseason_active(storage, "nba", lambda e: e["playoff"], self.TODAY)
        since = storage.get_all_events.call_args_list[-1].kwargs["since_date"]
        assert since == "2026-05-08"

    def test_regular_season_games_alone_mean_inactive(self):
        storage = self._storage(scheduled=[_event("a", "b", playoff=False)], completed=[])
        assert not bracket_projection.postseason_active(storage, "nba", lambda e: e["playoff"], self.TODAY)
