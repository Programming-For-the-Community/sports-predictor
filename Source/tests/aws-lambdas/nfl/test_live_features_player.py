"""
Unit tests for live_features.build_live_player_features -- the
single-player feature row the inference Lambda's manually-queried
player-prop route (GET /nfl/predictions/events/{event_id}/players/
{entity_id}) uses. FeatureStorage is mocked. Split out of what used to be
one large test_live_features.py -- see test_live_features_event.py's own
history note.
"""
from unittest.mock import MagicMock

import pytest

import live_features


def _event(
    event_key, event_date, home_id, away_id, home_score=None, away_score=None,
    home_depth_chart=None, away_depth_chart=None, home_injuries=None, away_injuries=None,
):
    home_result = {"score": home_score, "won": home_score is not None and home_score > away_score}
    away_result = {"score": away_score, "won": away_score is not None and away_score > home_score}
    return {
        "event_key": event_key,
        "event_date": event_date,
        "week": 5,
        "season_type": 2,
        "venue_indoor": False,
        "venue_city": "Kansas City",
        "venue_state": "MO",
        "weather_temperature": 40,
        "home_depth_chart": home_depth_chart,
        "away_depth_chart": away_depth_chart,
        "home_injuries": home_injuries,
        "away_injuries": away_injuries,
        "participants": [
            {"entity_id": home_id, "role": "home", "result": home_result},
            {"entity_id": away_id, "role": "away", "result": away_result},
        ],
    }


class TestBuildLivePlayerFeatures:
    def test_raises_when_event_not_found(self):
        storage = MagicMock()
        storage.get_event.return_value = None

        with pytest.raises(live_features.EventNotFoundError):
            live_features.build_live_player_features(storage, "nfl", "SPORT#NFL#EVENT#missing", "mahomes-patrick")

    def test_raises_when_entity_not_found(self):
        storage = MagicMock()
        storage.get_event.return_value = _event("E1", "2025-09-14", "KC", "LAC")
        storage.get_entity.return_value = None

        with pytest.raises(live_features.EventNotFoundError):
            live_features.build_live_player_features(storage, "nfl", "E1", "unknown-player")

    def test_team_id_comes_from_the_entity_record_not_the_last_game_log(self):
        storage = MagicMock()
        storage.get_event.return_value = _event("E1", "2025-09-14", "KC", "LAC")
        # Entity record says LAC (e.g. just traded) -- a stale last-game
        # log showing KC must NOT override this.
        storage.get_entity.return_value = {"entity_id": "mahomes-patrick", "metadata": {"team_id": "LAC"}}
        storage.get_player_game_stats.return_value = [
            {"event_date": "2025-09-07", "stat_line": {"passing_yards": 280}, "started": True},
        ]
        storage.get_team_events.return_value = []
        storage.get_all_events.return_value = []

        row = live_features.build_live_player_features(storage, "nfl", "E1", "mahomes-patrick")

        assert row["team_id"] == "LAC"
        assert row["opponent_id"] == "KC"  # LAC is away in this event -> opponent is home (KC)

    def test_avg_stats_reflect_the_players_own_prior_games(self):
        storage = MagicMock()
        storage.get_event.return_value = _event("E2", "2025-09-14", "KC", "LAC")
        storage.get_entity.return_value = {"entity_id": "mahomes-patrick", "metadata": {"team_id": "KC"}}
        storage.get_player_game_stats.return_value = [
            {"event_date": "2025-09-07", "stat_line": {"passing_yards": 280}, "started": True},
        ]
        storage.get_team_events.return_value = []
        storage.get_all_events.return_value = []

        row = live_features.build_live_player_features(storage, "nfl", "E2", "mahomes-patrick")

        assert row["avg_passing_yards"] == 280
        assert row["label_stat_line"] == {}  # unknown -- this is what's being predicted
