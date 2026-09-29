"""
Endpoint wiring for the thin per-sport ESPN clients that don't have their
own test file -- each method just picks an ESPN path and query params.
"""
from unittest.mock import patch

from library.http.f1_espn import F1EspnClient
from library.http.nba import NBAClient


class TestNBAClient:
    def test_endpoints(self):
        client = NBAClient(min_interval_seconds=0)
        with patch.object(client, "_get", return_value={"ok": 1}) as get:
            assert client.get_teams() == {"ok": 1}
            client.get_scoreboard_for_date("20260101")
            client.get_summary("401")
            client.get_roster("13")

        assert [c.args for c in get.call_args_list] == [
            ("teams",), ("scoreboard",), ("summary",), ("teams/13/roster",),
        ]
        assert [c.kwargs["params"] for c in get.call_args_list] == [{}, {"dates": "20260101"}, {"event": "401"}, {}]
        assert "basketball/nba" in client.base_url


class TestF1EspnClient:
    def test_scoreboard_asks_for_the_whole_season(self):
        client = F1EspnClient(min_interval_seconds=0)
        with patch.object(client, "_get", return_value={"events": []}) as get:
            assert client.get_scoreboard(2026) == {"events": []}

        get.assert_called_once_with("scoreboard", params={"dates": 2026})
        assert "racing/f1" in client.base_url
