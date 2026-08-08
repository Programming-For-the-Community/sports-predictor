"""
Unit tests for EspnCoreApiClient -- mocks get_absolute/_get directly rather
than the underlying requests.Session, since what matters here is the
$ref-resolution and shape-mapping logic, not HttpClient's own retry/rate-
limit behavior (already covered by this client not overriding it).
"""
from unittest.mock import MagicMock, patch

from library.http.espn_core import EspnCoreApiClient, _id_from_ref


class TestIdFromRef:
    def test_extracts_trailing_numeric_id(self):
        assert _id_from_ref("http://sports.core.api.espn.com/.../teams/14?lang=en&region=us") == "14"

    def test_extracts_id_with_no_query_string(self):
        assert _id_from_ref("http://sports.core.api.espn.com/.../athletes/4912218") == "4912218"

    def test_none_for_none_input(self):
        assert _id_from_ref(None) is None

    def test_none_for_url_with_no_trailing_id(self):
        assert _id_from_ref("http://sports.core.api.espn.com/.../coaches") is None


class TestGetSeasonCoaches:
    def _client(self):
        with patch("library.http.espn_core.HttpClient.__init__", return_value=None):
            client = EspnCoreApiClient()
        return client

    def test_resolves_coach_and_record_refs_into_per_team_dict(self):
        client = self._client()
        client._get = MagicMock(return_value={
            "items": [{"$ref": "http://.../coaches/4872749?lang=en"}],
        })

        def fake_get_absolute(url, params=None):
            if "coaches/4872749" in url:
                return {
                    "id": "4872749", "firstName": "Mike", "lastName": "LaFleur",
                    "team": {"$ref": "http://.../seasons/2025/teams/14?lang=en"},
                    "experience": 0,
                    "records": [{"record": {"$ref": "http://.../record?lang=en"}}],
                }
            if "record" in url:
                return {"summary": "8-9-0", "value": 0.4706}
            raise AssertionError(f"Unexpected URL {url}")

        client.get_absolute = MagicMock(side_effect=fake_get_absolute)

        result = client.get_season_coaches(2025)

        assert result == {
            "14": {
                "coach_id": "4872749",
                "coach_name": "Mike LaFleur",
                "experience": 0,
                "season_win_pct": 0.4706,
            },
        }

    def test_coach_missing_team_ref_is_skipped(self):
        client = self._client()
        client._get = MagicMock(return_value={"items": [{"$ref": "http://.../coaches/1"}]})
        client.get_absolute = MagicMock(return_value={"id": "1", "firstName": "No", "lastName": "Team"})

        result = client.get_season_coaches(2025)

        assert result == {}

    def test_coach_missing_record_ref_gets_none_win_pct(self):
        client = self._client()
        client._get = MagicMock(return_value={"items": [{"$ref": "http://.../coaches/1"}]})
        client.get_absolute = MagicMock(return_value={
            "id": "1", "firstName": "Andy", "lastName": "Reid",
            "team": {"$ref": "http://.../teams/12"},
            "experience": 27,
            "records": [],
        })

        result = client.get_season_coaches(2025)

        assert result["12"]["season_win_pct"] is None
        assert result["12"]["experience"] == 27

    def test_empty_listing_returns_empty_dict(self):
        client = self._client()
        client._get = MagicMock(return_value={"items": []})

        assert client.get_season_coaches(2025) == {}


class TestGetTeamInjuries:
    def _client(self):
        with patch("library.http.espn_core.HttpClient.__init__", return_value=None):
            client = EspnCoreApiClient()
        return client

    def test_resolves_injury_refs_into_entity_id_status_pairs(self):
        client = self._client()
        client._get = MagicMock(return_value={"items": [{"$ref": "http://.../injuries/1"}]})
        client.get_absolute = MagicMock(return_value={
            "status": "Questionable",
            "athlete": {"$ref": "http://.../athletes/4912218?lang=en"},
        })

        result = client.get_team_injuries("12")

        assert result == [{"entity_id": "4912218", "status": "Questionable"}]

    def test_injury_missing_athlete_ref_is_skipped(self):
        client = self._client()
        client._get = MagicMock(return_value={"items": [{"$ref": "http://.../injuries/1"}]})
        client.get_absolute = MagicMock(return_value={"status": "Out"})

        assert client.get_team_injuries("12") == []

    def test_empty_listing_returns_empty_list(self):
        client = self._client()
        client._get = MagicMock(return_value={"items": []})

        assert client.get_team_injuries("12") == []

    def test_recovered_active_status_is_filtered_out(self):
        # ESPN's endpoint returns the whole season's status-change log,
        # not just who's hurt now -- "Active" means recovered.
        client = self._client()
        client._get = MagicMock(return_value={"items": [{"$ref": "http://.../injuries/1"}]})
        client.get_absolute = MagicMock(return_value={
            "status": "Active",
            "athlete": {"$ref": "http://.../athletes/1?lang=en"},
        })

        assert client.get_team_injuries("12") == []

    def test_doubtful_status_is_kept(self):
        client = self._client()
        client._get = MagicMock(return_value={"items": [{"$ref": "http://.../injuries/1"}]})
        client.get_absolute = MagicMock(return_value={
            "status": "Doubtful",
            "athlete": {"$ref": "http://.../athletes/1?lang=en"},
        })

        assert client.get_team_injuries("12") == [{"entity_id": "1", "status": "Doubtful"}]

    def test_requests_limit_100_to_get_full_season_log_in_one_call(self):
        client = self._client()
        client._get = MagicMock(return_value={"items": []})

        client.get_team_injuries("12")

        client._get.assert_called_once_with("teams/12/injuries", {"limit": 100})
