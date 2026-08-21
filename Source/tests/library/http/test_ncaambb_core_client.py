"""
Unit tests for library.http.ncaambb_core -- NCAAMBBCoreClient's AP-poll
fetch (single-attempt, 404-tolerant, see the module's own docstring for
why it bypasses HttpClient._get's retry-with-backoff) and the pure
parsing helpers (season_type_week_from_ref, ap_poll_to_rank_by_team).
"""
from unittest.mock import MagicMock

from library.http.ncaambb_core import (
    NCAAMBBCoreClient,
    ap_poll_to_rank_by_team,
    current_ap_poll_pointer,
    season_type_week_from_ref,
)


class TestGetApPoll:
    def _client_with_mock_session(self):
        # __new__ (not __init__) so no real HttpClient construction (no
        # real requests.Session, no real RateLimiter) happens -- both are
        # replaced with mocks directly, same pattern test_ncaambb_client.py
        # uses for _get.
        client = NCAAMBBCoreClient.__new__(NCAAMBBCoreClient)
        client.base_url = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/mens-college-basketball"
        client._session = MagicMock()
        client._rate_limiter = MagicMock()
        return client

    def test_returns_the_poll_on_200(self):
        client = self._client_with_mock_session()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"name": "AP Top 25", "ranks": []}
        client._session.get.return_value = response

        result = client.get_ap_poll(2026, 2, 5)

        assert result == {"name": "AP Top 25", "ranks": []}
        called_url = client._session.get.call_args.args[0]
        assert called_url.endswith("/seasons/2026/types/2/weeks/5/rankings/1")

    def test_returns_none_on_404_without_raising(self):
        client = self._client_with_mock_session()
        response = MagicMock()
        response.status_code = 404
        client._session.get.return_value = response

        result = client.get_ap_poll(2026, 2, 25)

        assert result is None
        response.raise_for_status.assert_not_called()

    def test_paces_through_the_shared_rate_limiter(self):
        client = self._client_with_mock_session()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {}
        client._session.get.return_value = response

        client.get_ap_poll(2026, 2, 1)

        client._rate_limiter.wait.assert_called_once()

    def test_a_non_404_error_status_raises(self):
        import requests

        client = self._client_with_mock_session()
        response = MagicMock()
        response.status_code = 500
        response.raise_for_status.side_effect = requests.HTTPError("500")
        client._session.get.return_value = response

        try:
            client.get_ap_poll(2026, 2, 1)
            assert False, "expected HTTPError to propagate"
        except requests.HTTPError:
            pass


class TestSeasonTypeWeekFromRef:
    def test_parses_season_type_week_out_of_a_real_ref(self):
        ref = "http://sports.core.api.espn.pvt/v2/sports/basketball/leagues/mens-college-basketball/seasons/2026/types/3/weeks/3/rankings/1?lang=en&region=us"
        assert season_type_week_from_ref(ref) == (2026, 3, 3)

    def test_none_for_a_ref_with_no_match(self):
        assert season_type_week_from_ref("http://example.com/nothing") is None

    def test_none_for_none_input(self):
        assert season_type_week_from_ref(None) is None


class TestApPollToRankByTeam:
    def test_extracts_team_id_and_rank_from_each_entry(self):
        poll = {
            "ranks": [
                {"current": 1, "team": {"$ref": "http://sports.core.api.espn.com/.../teams/130?lang=en&region=us"}},
                {"current": 2, "team": {"$ref": "http://sports.core.api.espn.com/.../teams/150?lang=en&region=us"}},
            ]
        }

        result = ap_poll_to_rank_by_team(poll)

        assert result == {"130": 1, "150": 2}

    def test_entry_missing_team_ref_is_skipped(self):
        poll = {"ranks": [{"current": 1, "team": {}}]}

        result = ap_poll_to_rank_by_team(poll)

        assert result == {}

    def test_empty_ranks_list_returns_empty_dict(self):
        assert ap_poll_to_rank_by_team({"ranks": []}) == {}

    def test_missing_ranks_key_returns_empty_dict(self):
        assert ap_poll_to_rank_by_team({}) == {}


class TestCurrentApPollPointer:
    def test_picks_the_ap_entry_by_type_not_position(self):
        response = {
            "rankings": [
                {"id": "2", "type": "usa", "$ref": "http://x/.../seasons/2026/types/3/weeks/1/rankings/2"},
                {"id": "1", "type": "ap", "$ref": "http://x/.../seasons/2026/types/3/weeks/3/rankings/1"},
            ]
        }

        assert current_ap_poll_pointer(response) == (2026, 3, 3)

    def test_none_when_no_ap_entry_present(self):
        response = {"rankings": [{"id": "2", "type": "usa", "$ref": "http://x/.../seasons/2026/types/3/weeks/1/rankings/2"}]}

        assert current_ap_poll_pointer(response) is None

    def test_none_for_empty_rankings(self):
        assert current_ap_poll_pointer({"rankings": []}) is None
