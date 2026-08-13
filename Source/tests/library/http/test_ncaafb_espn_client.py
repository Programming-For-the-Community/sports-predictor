"""
Unit tests for library.http.ncaafb_espn -- NcaafbEspnClient's sport_path
wiring and its one endpoint method. EspnBaseClient's own root-url/user-
agent resolution is already covered by test_espn_client.py; this only
verifies what NcaafbEspnClient adds on top.
"""
from unittest.mock import MagicMock, patch

from library.http.ncaafb_espn import NcaafbEspnClient


class TestNcaafbEspnClient:
    def test_uses_college_football_sport_path(self):
        with patch("library.http.espn.HttpClient.__init__", return_value=None) as mock_init:
            NcaafbEspnClient()

        assert mock_init.call_args.kwargs["base_url"].endswith("/football/college-football")

    def test_get_scoreboard_for_date_passes_dates_param(self):
        client = NcaafbEspnClient.__new__(NcaafbEspnClient)
        client._get = MagicMock(return_value={"events": []})

        result = client.get_scoreboard_for_date("20260906")

        client._get.assert_called_once_with("scoreboard", params={"dates": "20260906"})
        assert result == {"events": []}

    def test_get_summary_passes_event_param(self):
        client = NcaafbEspnClient.__new__(NcaafbEspnClient)
        client._get = MagicMock(return_value={"header": {}})

        result = client.get_summary("401520281")

        client._get.assert_called_once_with("summary", params={"event": "401520281"})
        assert result == {"header": {}}
