"""
Unit tests for library.http.ncaambb -- NCAAMBBClient's sport_path wiring
and its endpoint methods. EspnBaseClient's own root-url/user-agent
resolution is already covered by test_espn_client.py; this only verifies
what NCAAMBBClient adds on top.

get_teams/get_scoreboard_for_date get dedicated params-are-correct tests,
unlike NBA's own (undocumented) client -- a real bug shipped past this
project's sandbox-only verification and was only caught by running the
live-ESPN integration suite on a real network (2026-08-20): both methods
silently omitted a required param (limit/groups) that NBA's own client
never needed at NBA's much smaller scale. These tests exist so that
regression can never quietly reappear.
"""
from unittest.mock import MagicMock, patch

from library.http.ncaambb import NCAAMBBClient


class TestNCAAMBBClient:
    def test_uses_mens_college_basketball_sport_path(self):
        with patch("library.http.espn.HttpClient.__init__", return_value=None) as mock_init:
            NCAAMBBClient()

        assert mock_init.call_args.kwargs["base_url"].endswith("/basketball/mens-college-basketball")

    def test_get_teams_passes_a_large_limit_to_avoid_espns_default_page_size(self):
        # ESPN defaults to 50 teams with no limit param -- D1 has ~362
        # (confirmed live, 2026-08-20). This isn't optional padding, it's
        # the difference between the full D1 field and a silently
        # truncated subset.
        client = NCAAMBBClient.__new__(NCAAMBBClient)
        client._get = MagicMock(return_value={"sports": []})

        result = client.get_teams()

        client._get.assert_called_once_with("teams", params={"limit": 1000})
        assert result == {"sports": []}

    def test_get_scoreboard_for_date_passes_the_division_i_groups_filter(self):
        # A bare dates-only call silently returns an unscoped/"featured"
        # subset, not the full D1 schedule (confirmed live, 2026-08-20 --
        # as few as ~10% of a full slate on a busy date). groups=50 is
        # ESPN's id for "NCAA Division I"; limit=400 is defensive padding
        # (see get_scoreboard_for_date's own docstring for why it stays
        # in despite not being individually confirmed necessary).
        client = NCAAMBBClient.__new__(NCAAMBBClient)
        client._get = MagicMock(return_value={"events": []})

        result = client.get_scoreboard_for_date("20260114")

        client._get.assert_called_once_with("scoreboard", params={"dates": "20260114", "groups": 50, "limit": 400})
        assert result == {"events": []}

    def test_get_summary_passes_event_param(self):
        client = NCAAMBBClient.__new__(NCAAMBBClient)
        client._get = MagicMock(return_value={"header": {}})

        result = client.get_summary("401746082")

        client._get.assert_called_once_with("summary", params={"event": "401746082"})
        assert result == {"header": {}}

    def test_get_roster_passes_team_id_in_path_with_no_extra_params(self):
        client = NCAAMBBClient.__new__(NCAAMBBClient)
        client._get = MagicMock(return_value={"athletes": []})

        result = client.get_roster("150")

        client._get.assert_called_once_with("teams/150/roster", params={})
        assert result == {"athletes": []}

    def test_get_current_rankings_pointer_passes_no_params(self):
        client = NCAAMBBClient.__new__(NCAAMBBClient)
        client._get = MagicMock(return_value={"rankings": []})

        result = client.get_current_rankings_pointer()

        client._get.assert_called_once_with("rankings", params={})
        assert result == {"rankings": []}
