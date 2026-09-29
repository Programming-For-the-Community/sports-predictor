"""
data-backfills/nfl/normalize.py only binds this sport's SPORT and stat-split
tables onto library.normalize.espn's shared normalizers.
"""
from unittest.mock import patch

import normalize


def test_each_wrapper_passes_this_sports_own_config():
    with patch.object(normalize, "_team_to_entity", return_value="team") as team,          patch.object(normalize, "_scoreboard_event_to_event_item", return_value="event") as event,          patch.object(normalize, "_boxscore_to_player_game_stats", return_value=([], [])) as player_stats,          patch.object(normalize, "_boxscore_to_team_game_stats", return_value=[]) as team_stats:
        assert normalize.team_to_entity({"id": "1"}) == "team"
        assert normalize.scoreboard_event_to_event_item({"id": "2"}) == "event"
        assert normalize.boxscore_to_player_game_stats({"s": 1}) == ([], [])
        assert normalize.boxscore_to_team_game_stats({"s": 1}) == []

    team.assert_called_once_with({"id": "1"}, "nfl")
    event.assert_called_once_with({"id": "2"}, "nfl")
    player_stats.assert_called_once_with({"s": 1}, "nfl", normalize._COMPOUND_KEY_SPLITS)
    team_stats.assert_called_once_with({"s": 1}, "nfl", normalize._TEAM_COMPOUND_KEY_SPLITS)
