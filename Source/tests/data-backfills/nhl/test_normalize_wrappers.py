"""
data-backfills/nhl/normalize.py only binds this sport's SPORT onto the
shared team normalizer and library.normalize.nhl's hockey normalizers.
"""
from unittest.mock import patch

import normalize


def test_each_wrapper_passes_this_sports_own_config():
    with patch.object(normalize, "_team_to_entity", return_value="team") as team, \
         patch.object(normalize.nhl, "scoreboard_event_to_event_item", return_value="event") as event, \
         patch.object(normalize.nhl, "boxscore_to_player_game_stats", return_value=([], [])) as player_stats, \
         patch.object(normalize.nhl, "boxscore_to_team_game_stats", return_value=[]) as team_stats:
        assert normalize.team_to_entity({"id": "1"}) == "team"
        assert normalize.scoreboard_event_to_event_item({"id": "2"}) == "event"
        assert normalize.boxscore_to_player_game_stats({"s": 1}) == ([], [])
        assert normalize.boxscore_to_team_game_stats({"s": 1}) == []

    team.assert_called_once_with({"id": "1"}, "nhl")
    event.assert_called_once_with({"id": "2"}, "nhl")
    player_stats.assert_called_once_with({"s": 1}, "nhl")
    team_stats.assert_called_once_with({"s": 1}, "nhl")
