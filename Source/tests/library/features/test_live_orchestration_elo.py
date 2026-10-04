from unittest.mock import MagicMock

from library.features.live_orchestration import live_elo_ratings


def _game(event_key, event_date, home_score=None, away_score=None):
    return {
        "event_key": event_key, "event_date": event_date,
        "participants": [
            {"entity_id": "KC", "role": "home", "result": {"score": home_score}},
            {"entity_id": "LAC", "role": "away", "result": {"score": away_score}},
        ],
    }


class TestLiveEloRatings:
    def test_computed_ratings_carry_past_events_and_this_events_current_ratings(self):
        past = _game("E1", "2025-09-07", 30, 10)
        live = _game("E2", "2025-09-14")

        ratings = live_elo_ratings(MagicMock(), "nfl", live, "KC", "LAC", events=[past, live])

        assert ratings["E1"]["home_pre_avg_points_scored"] is None
        assert ratings["E2"]["home_pre_rating"] > 1500
        assert "home_pre_avg_points_scored" not in ratings["E2"]

    def test_precomputed_ratings_give_only_this_event(self):
        ratings = live_elo_ratings(MagicMock(), "nfl", _game("E2", "2025-09-14"), "KC", "LAC", current_ratings={"KC": 1600.0})

        assert ratings == {"E2": {"home_pre_rating": 1600.0, "away_pre_rating": 1500.0}}
