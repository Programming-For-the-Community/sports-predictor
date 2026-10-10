"""
Unit tests for library.serving.live_scores_common.SportLiveScores -- one
sport's cache key, compound-key splits and worker count bound to the
module's functions.
"""
from unittest.mock import patch

from library.serving import live_scores_common as common

_SPLITS = {"a-b": ("a", "b")}


def test_each_method_passes_the_sports_own_settings():
    live = common.SportLiveScores("nba/cache/live.json", _SPLITS, 7)

    with patch.object(common, "get_cache", return_value={"x": 1}) as get_cache, \
            patch.object(common, "put_cache") as put_cache, \
            patch.object(common, "live_player_stats", return_value={}) as player_stats, \
            patch.object(common, "refresh", return_value={"polled": 1}) as refresh, \
            patch.object(common, "get_live_scores", return_value={"events": {}}) as get_live:
        assert live.get_cache("s3", "raw") == {"x": 1}
        live.put_cache("s3", "raw", {"y": 2})
        assert live.live_player_stats("client", "nba", "e1") == {}
        assert live.refresh("storage", "s3", "raw", "client", "nba") == {"polled": 1}
        assert live.get_live_scores("s3", "raw") == {"events": {}}

    get_cache.assert_called_once_with("s3", "raw", "nba/cache/live.json")
    put_cache.assert_called_once_with("s3", "raw", "nba/cache/live.json", {"y": 2})
    player_stats.assert_called_once_with("client", "nba", "e1", _SPLITS)
    refresh.assert_called_once_with("storage", "s3", "raw", "client", "nba", "nba/cache/live.json", _SPLITS, 7)
    get_live.assert_called_once_with("s3", "raw", "nba/cache/live.json")


def test_a_sports_own_box_score_parser_is_passed_through():
    def parse(summary, sport):
        return [{"entity_id": "g1", "stat_line": {"saves": 30, "save_pct": "--"}}], []

    live = common.SportLiveScores("nhl/cache/live.json", {}, 7, parse)
    client = type("Client", (), {"get_summary": staticmethod(lambda event_id: {"id": event_id})})()

    assert live.live_player_stats(client, "nhl", "e1") == {"g1": {"saves": 30}}

    with patch.object(common, "refresh", return_value={"polled": 0}) as refresh:
        live.refresh("storage", "s3", "raw", "client", "nhl")

    refresh.assert_called_once_with("storage", "s3", "raw", "client", "nhl", "nhl/cache/live.json", {}, 7, parse_boxscore=parse)
