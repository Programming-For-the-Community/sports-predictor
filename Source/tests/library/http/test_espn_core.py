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
        # Not set by the patched-out __init__ above -- career_playoff_win_pct's
        # own URL is built directly from base_url (see get_season_coaches'
        # own comment for why), so every test needs one.
        client.base_url = "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
        return client

    def _coach_detail(self, coach_id: str, first: str, last: str, team_id: str, experience: int,
                       season_record_ref: str | None = None) -> dict:
        records = [{"record": {"$ref": season_record_ref}}] if season_record_ref else []
        return {
            "id": coach_id, "firstName": first, "lastName": last,
            "team": {"$ref": f"http://.../seasons/2025/teams/{team_id}?lang=en"},
            "experience": experience,
            "records": records,
        }

    def test_resolves_coach_and_record_refs_into_per_team_dict(self):
        client = self._client()
        client._get = MagicMock(return_value={
            "items": [{"$ref": "http://.../coaches/4872749?lang=en"}],
        })

        def fake_get_absolute(url, params=None):
            if url.endswith("/record/3"):
                return {"summary": "0-0-0"}  # no career playoff games -- no "value" key, confirmed live
            if "seasons/2025/types/2" in url:
                return {"summary": "8-9-0", "value": 0.4706}
            if "coaches/4872749" in url:
                return self._coach_detail(
                    "4872749", "Mike", "LaFleur", "14", 0,
                    "http://.../seasons/2025/types/2/coaches/4872749/record?lang=en",
                )
            raise AssertionError(f"Unexpected URL {url}")

        client.get_absolute = MagicMock(side_effect=fake_get_absolute)

        result = client.get_season_coaches(2025)

        assert result == {
            "14": {
                "coach_id": "4872749",
                "coach_name": "Mike LaFleur",
                "experience": 0,
                "season_win_pct": 0.4706,
                "career_playoff_win_pct": None,
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
        client.get_absolute = MagicMock(side_effect=lambda url, params=None: (
            {"summary": "0-0-0"} if url.endswith("/record/3") else
            self._coach_detail("1", "Andy", "Reid", "12", 27)
        ))

        result = client.get_season_coaches(2025)

        assert result["12"]["season_win_pct"] is None
        assert result["12"]["experience"] == 27
        assert result["12"]["career_playoff_win_pct"] is None

    def test_empty_listing_returns_empty_dict(self):
        client = self._client()
        client._get = MagicMock(return_value={"items": []})

        assert client.get_season_coaches(2025) == {}

    def test_regular_season_record_is_used_not_postseason(self):
        # A coach whose team made the playoffs has TWO SEASON records
        # entries -- confirmed live (Sean McVay, 2025 Rams: regular season
        # 12-5 = .706, postseason 1-1 = .5). Postseason listed AFTER
        # regular season here specifically because that's the order that
        # used to silently win (last dict-comprehension write wins) before
        # this was filtered to season type 2.
        client = self._client()
        client._get = MagicMock(return_value={"items": [{"$ref": "http://.../coaches/2499338"}]})
        client.get_absolute = MagicMock(side_effect=lambda url, params=None: {
            "http://.../coaches/2499338": {
                "id": "2499338", "firstName": "Sean", "lastName": "McVay",
                "team": {"$ref": "http://.../seasons/2025/teams/14?lang=en"},
                "experience": 9,
                "records": [
                    {"record": {"$ref": "http://.../seasons/2025/types/2/coaches/2499338/record?lang=en"}},
                    {"record": {"$ref": "http://.../seasons/2025/types/3/coaches/2499338/record?lang=en"}},
                ],
            },
            "http://.../seasons/2025/types/2/coaches/2499338/record?lang=en": {"summary": "12-5-0", "value": 0.7058823529},
            "http://.../seasons/2025/types/3/coaches/2499338/record?lang=en": {"summary": "1-1-0", "value": 0.5},
            f"{client.base_url}/coaches/2499338/record/3": {"summary": "0-0-0"},
        }[url])

        result = client.get_season_coaches(2025)

        assert result["14"]["season_win_pct"] == 0.7058823529

    def test_career_playoff_win_pct_reflects_the_coachs_whole_career_not_this_season(self):
        # A separate, THIRD $ref resolution round -- lives on the coach's
        # own person-level resource, not the season-scoped one, and covers
        # every postseason game across every team/season this coach has
        # ever coached (confirmed live: McVay's 2025 season postseason
        # alone was 1-1 = .5, but his CAREER postseason record is 10-6 =
        # .625 -- these two numbers are deliberately different things).
        client = self._client()
        client._get = MagicMock(return_value={"items": [{"$ref": "http://.../coaches/2499338"}]})
        client.get_absolute = MagicMock(side_effect=lambda url, params=None: {
            "http://.../coaches/2499338": self._coach_detail(
                "2499338", "Sean", "McVay", "14", 9,
                "http://.../seasons/2025/types/2/coaches/2499338/record?lang=en",
            ),
            "http://.../seasons/2025/types/2/coaches/2499338/record?lang=en": {"summary": "12-5-0", "value": 0.7058823529},
            f"{client.base_url}/coaches/2499338/record/3": {"summary": "10-6-0", "value": 0.625},
        }[url])

        result = client.get_season_coaches(2025)

        assert result["14"]["career_playoff_win_pct"] == 0.625

    def test_zero_career_playoff_games_is_none_not_a_zero_division_crash(self):
        client = self._client()
        client._get = MagicMock(return_value={"items": [{"$ref": "http://.../coaches/1"}]})
        client.get_absolute = MagicMock(side_effect=lambda url, params=None: (
            # No "value" key on a 0-0-0 record -- confirmed live.
            {"summary": "0-0-0"} if url.endswith("/record/3") else
            self._coach_detail("1", "Rookie", "Coach", "12", 0)
        ))

        result = client.get_season_coaches(2025)

        assert result["12"]["career_playoff_win_pct"] is None


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
