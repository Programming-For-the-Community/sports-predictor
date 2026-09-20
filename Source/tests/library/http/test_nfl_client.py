"""
Unit tests for library.http.nfl -- NFLClient's sport_path wiring and its
five endpoint methods. EspnBaseClient's own root-url/user-agent resolution
is already covered by test_espn_client.py; this only verifies what
NFLClient adds on top.
"""
from unittest.mock import patch

from library.http.nfl import NFLClient


class TestNFLClient:
    def test_uses_the_football_nfl_sport_path(self):
        with patch("library.http.espn.HttpClient.__init__", return_value=None) as mock_init:
            NFLClient()

        assert mock_init.call_args.kwargs["base_url"].endswith("/football/nfl")

    def test_get_teams_passes_no_params(self):
        client = NFLClient.__new__(NFLClient)
        client._get = lambda *a, **kw: ("teams", a, kw)

        result = client.get_teams()

        assert result == ("teams", ("teams",), {"params": {}})

    def test_get_scoreboard_passes_year_seasontype_and_week(self):
        client = NFLClient.__new__(NFLClient)
        client._get = lambda *a, **kw: (a, kw)

        result = client.get_scoreboard(2025, 2, 3)

        assert result == (("scoreboard",), {"params": {"dates": 2025, "seasontype": 2, "week": 3}})

    def test_get_scoreboard_for_date_passes_dates_param_only(self):
        client = NFLClient.__new__(NFLClient)
        client._get = lambda *a, **kw: (a, kw)

        result = client.get_scoreboard_for_date("20260112")

        assert result == (("scoreboard",), {"params": {"dates": "20260112"}})

    def test_get_summary_passes_event_param(self):
        client = NFLClient.__new__(NFLClient)
        client._get = lambda *a, **kw: (a, kw)

        result = client.get_summary("401671800")

        assert result == (("summary",), {"params": {"event": "401671800"}})

    def test_get_depth_chart_passes_team_id_in_path(self):
        client = NFLClient.__new__(NFLClient)
        client._get = lambda *a, **kw: (a, kw)

        result = client.get_depth_chart("KC")

        assert result == (("teams/KC/depthcharts",), {"params": {}})

    def test_get_roster_passes_team_id_in_path(self):
        client = NFLClient.__new__(NFLClient)
        client._get = lambda *a, **kw: (a, kw)

        result = client.get_roster("KC")

        assert result == (("teams/KC/roster",), {"params": {}})
