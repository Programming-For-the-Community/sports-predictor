"""
Unit tests for library.http.pga -- PGAClient's sport_path wiring and its
endpoint methods. EspnBaseClient's own root-url/user-agent resolution
is already covered by test_espn_client.py; this only verifies what
PGAClient adds on top.

sport_path is "golf" (not "golf/pga", unlike every other sport client's
sport-specific-looking path) specifically because the two endpoints live
under different sub-paths of that same "golf" root -- see PGAClient's own
docstring for why a single sport_path of "golf/pga" wouldn't work for
get_leaderboard.
"""
from unittest.mock import MagicMock, patch

from library.http.pga import PGAClient


class TestPGAClient:
    def test_uses_the_bare_golf_sport_path(self):
        with patch("library.http.espn.HttpClient.__init__", return_value=None) as mock_init:
            PGAClient()

        assert mock_init.call_args.kwargs["base_url"].endswith("/golf")

    def test_get_scoreboard_for_date_passes_dates_param_under_pga_scoreboard(self):
        client = PGAClient.__new__(PGAClient)
        client._get = MagicMock(return_value={"events": []})

        result = client.get_scoreboard_for_date("20260824")

        client._get.assert_called_once_with("pga/scoreboard", params={"dates": "20260824"})
        assert result == {"events": []}

    def test_get_leaderboard_passes_event_param(self):
        client = PGAClient.__new__(PGAClient)
        client._get = MagicMock(return_value={"events": [{"id": "401811963"}]})

        result = client.get_leaderboard("401811963")

        client._get.assert_called_once_with("leaderboard", params={"event": "401811963"})
        assert result == {"events": [{"id": "401811963"}]}

    def test_get_statistics_passes_no_params_under_pga_statistics(self):
        client = PGAClient.__new__(PGAClient)
        client._get = MagicMock(return_value={"stats": {"categories": []}})

        result = client.get_statistics()

        client._get.assert_called_once_with("pga/statistics", params={})
        assert result == {"stats": {"categories": []}}
