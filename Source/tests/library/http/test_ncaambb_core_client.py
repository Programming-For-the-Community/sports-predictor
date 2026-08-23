"""
Unit tests for library.http.ncaambb_core -- NCAAMBBCoreClient's AP-poll
fetch (single-attempt, 404-tolerant, see the module's own docstring for
why it bypasses HttpClient._get's retry-with-backoff), its conference-
membership discovery methods (group children -> group detail -> group
teams, all confirmed live 2026-08-22), and the pure parsing/aggregation
helpers (season_type_week_from_ref, ap_poll_to_rank_by_team,
resolve_conference_membership).
"""
from unittest.mock import MagicMock

from library.http.ncaambb_core import (
    NCAAMBBCoreClient,
    ap_poll_to_rank_by_team,
    current_ap_poll_pointer,
    resolve_conference_membership,
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


class TestConferenceGroupDiscovery:
    def _client_with_mock_session(self):
        client = NCAAMBBCoreClient.__new__(NCAAMBBCoreClient)
        client.base_url = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/mens-college-basketball"
        client._session = MagicMock()
        client._rate_limiter = MagicMock()
        return client

    def test_get_conference_group_refs_returns_item_refs(self):
        client = self._client_with_mock_session()
        response = MagicMock()
        response.json.return_value = {
            "items": [
                {"$ref": ".../groups/2?lang=en&region=us"},
                {"$ref": ".../groups/3?lang=en&region=us"},
            ]
        }
        client._session.get.return_value = response

        refs = client.get_conference_group_refs(2026)

        assert refs == [".../groups/2?lang=en&region=us", ".../groups/3?lang=en&region=us"]
        called_url = client._session.get.call_args.args[0]
        assert called_url.endswith("/seasons/2026/types/2/groups/50/children")

    def test_get_conference_group_refs_defaults_to_regular_season_type(self):
        client = self._client_with_mock_session()
        response = MagicMock()
        response.json.return_value = {"items": []}
        client._session.get.return_value = response

        client.get_conference_group_refs(2026)

        called_url = client._session.get.call_args.args[0]
        assert "/types/2/" in called_url

    def test_get_group_detail_returns_the_raw_dict(self):
        client = self._client_with_mock_session()
        response = MagicMock()
        response.json.return_value = {"name": "Atlantic Coast Conference", "shortName": "ACC", "isConference": True}
        client._session.get.return_value = response

        detail = client.get_group_detail("https://.../groups/2")

        assert detail["shortName"] == "ACC"
        assert detail["isConference"] is True

    def test_get_group_team_refs_returns_item_refs(self):
        client = self._client_with_mock_session()
        response = MagicMock()
        response.json.return_value = {"items": [{"$ref": ".../teams/150"}, {"$ref": ".../teams/259"}]}
        client._session.get.return_value = response

        refs = client.get_group_team_refs("https://.../groups/2/teams")

        assert refs == [".../teams/150", ".../teams/259"]


class TestResolveConferenceMembership:
    def _client(self, conference_refs: list[str], details: dict[str, dict], team_refs: dict[str, list[str]]):
        client = MagicMock()
        client.get_conference_group_refs.return_value = conference_refs
        client.get_group_detail.side_effect = lambda ref: details[ref]
        client.get_group_team_refs.side_effect = lambda teams_ref: team_refs[teams_ref]
        return client

    def test_builds_team_id_to_conference_name_from_the_group_hierarchy(self):
        client = self._client(
            conference_refs=["ref/groups/2"],
            details={"ref/groups/2": {"isConference": True, "shortName": "ACC", "teams": {"$ref": "ref/groups/2/teams"}}},
            team_refs={"ref/groups/2/teams": ["https://.../teams/150", "https://.../teams/259"]},
        )

        result = resolve_conference_membership(client, 2026)

        assert result == {"150": "ACC", "259": "ACC"}

    def test_a_group_that_is_not_a_conference_is_skipped(self):
        client = self._client(
            conference_refs=["ref/groups/50"],
            details={"ref/groups/50": {"isConference": False, "shortName": "Division I", "teams": {"$ref": "ref/groups/50/teams"}}},
            team_refs={"ref/groups/50/teams": ["https://.../teams/1"]},
        )

        result = resolve_conference_membership(client, 2026)

        assert result == {}

    def test_multiple_conferences_are_merged_into_one_result(self):
        client = self._client(
            conference_refs=["ref/groups/2", "ref/groups/3"],
            details={
                "ref/groups/2": {"isConference": True, "shortName": "ACC", "teams": {"$ref": "ref/groups/2/teams"}},
                "ref/groups/3": {"isConference": True, "shortName": "Big Ten", "teams": {"$ref": "ref/groups/3/teams"}},
            },
            team_refs={
                "ref/groups/2/teams": ["https://.../teams/150"],
                "ref/groups/3/teams": ["https://.../teams/275"],
            },
        )

        result = resolve_conference_membership(client, 2026)

        assert result == {"150": "ACC", "275": "Big Ten"}

    def test_one_conference_failing_does_not_lose_the_others(self):
        client = self._client(
            conference_refs=["ref/groups/2", "ref/groups/3"],
            details={"ref/groups/3": {"isConference": True, "shortName": "Big Ten", "teams": {"$ref": "ref/groups/3/teams"}}},
            team_refs={"ref/groups/3/teams": ["https://.../teams/275"]},
        )
        # groups/2 was never registered in `details`, so its lookup raises KeyError,
        # simulating a real fetch failure for that one conference.

        result = resolve_conference_membership(client, 2026)

        assert result == {"275": "Big Ten"}


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
