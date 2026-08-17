"""
Unit tests for library.storage.ncaafb_team_cache -- shared by
aws-lambdas/ncaafb/ingest/enrichment.py and aws-lambdas/ncaafb/
schedule-sync/handler.py (see this module's own docstring for why it has
to live in the shared library package, not a sibling Lambda's local
file). All AWS/CFBD calls are mocked.
"""
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import MagicMock

from botocore.exceptions import ClientError

from library.storage.ncaafb_team_cache import attach_venue_indoor, get_cached_teams, teams_by_id, teams_by_school

BUCKET = "test-bucket"


def _make_s3():
    mock_s3 = MagicMock()
    store: dict[str, bytes] = {}

    def _get(**kwargs):
        key = kwargs.get("Key")
        if key in store:
            return {"Body": BytesIO(store[key])}
        raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "GetObject")

    def _put(**kwargs):
        body = kwargs.get("Body")
        store[kwargs.get("Key")] = body.encode("utf-8") if isinstance(body, str) else body

    mock_s3.get_object.side_effect = _get
    mock_s3.put_object.side_effect = _put
    return mock_s3


def _prime_cache(mock_s3, key: str, data, fetched_at: str = "2026-01-01T00:00:00+00:00") -> None:
    mock_s3.put_object(Key=key, Body=json.dumps({"fetched_at": fetched_at, "data": data}))


def _team(
    team_id: str, school: str, conference: str = "SEC", dome: bool | None = False,
    city: str | None = None, state: str | None = None,
) -> dict:
    return {
        "id": team_id, "school": school, "conference": conference,
        "location": {"dome": dome, "city": city, "state": state},
    }


class TestGetCachedTeams:
    def test_cache_miss_fetches_and_caches(self):
        mock_s3 = _make_s3()
        client = MagicMock()
        client.get_teams.return_value = [_team("2", "Georgia")]

        result = get_cached_teams(mock_s3, BUCKET, client, 2025)

        assert result == [_team("2", "Georgia")]
        client.get_teams.assert_called_once_with(2025)
        mock_s3.put_object.assert_called_once()

    def test_fresh_cache_hit_does_not_call_the_client(self):
        mock_s3 = _make_s3()
        fresh = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        _prime_cache(mock_s3, "ncaafb/cache/season-teams/2025.json", [{"id": "2", "school": "cached"}], fetched_at=fresh)
        client = MagicMock()

        result = get_cached_teams(mock_s3, BUCKET, client, 2025)

        assert result == [{"id": "2", "school": "cached"}]
        client.get_teams.assert_not_called()

    def test_stale_cache_refetches(self):
        mock_s3 = _make_s3()
        stale = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        _prime_cache(mock_s3, "ncaafb/cache/season-teams/2025.json", [{"id": "2", "school": "stale"}], fetched_at=stale)
        client = MagicMock()
        client.get_teams.return_value = [_team("2", "Georgia")]

        get_cached_teams(mock_s3, BUCKET, client, 2025)

        client.get_teams.assert_called_once_with(2025)


class TestTeamsById:
    def test_keys_by_string_id(self):
        result = teams_by_id([_team("2", "Georgia"), _team(52, "Alabama")])
        assert set(result.keys()) == {"2", "52"}

    def test_skips_entries_missing_id(self):
        result = teams_by_id([{"school": "No Id Team"}])
        assert result == {}


class TestTeamsBySchool:
    def test_keys_by_school_name(self):
        result = teams_by_school([_team("2", "Georgia"), _team(52, "Alabama")])
        assert set(result.keys()) == {"Georgia", "Alabama"}

    def test_skips_entries_missing_school(self):
        result = teams_by_school([{"id": "2"}])
        assert result == {}


class TestAttachVenueIndoor:
    def test_sets_venue_indoor_from_home_teams_dome_flag(self):
        mock_s3 = _make_s3()
        client = MagicMock()
        client.get_teams.return_value = [_team("2", "Georgia", dome=False), _team("52", "Alabama", dome=False)]
        games = [{"id": "1", "homeId": "2", "awayId": "52"}]

        attach_venue_indoor(games, 2025, client, mock_s3, BUCKET)

        assert games[0]["venue_indoor"] is False

    def test_sets_true_for_a_dome_home_team(self):
        mock_s3 = _make_s3()
        client = MagicMock()
        client.get_teams.return_value = [_team("2", "Syracuse", dome=True), _team("52", "Alabama", dome=False)]
        games = [{"id": "1", "homeId": "2", "awayId": "52"}]

        attach_venue_indoor(games, 2025, client, mock_s3, BUCKET)

        assert games[0]["venue_indoor"] is True

    def test_none_when_home_team_not_found_in_season_teams(self):
        mock_s3 = _make_s3()
        client = MagicMock()
        client.get_teams.return_value = []
        games = [{"id": "1", "homeId": "999", "awayId": "52"}]

        attach_venue_indoor(games, 2025, client, mock_s3, BUCKET)

        assert games[0]["venue_indoor"] is None
        assert games[0]["venue_city"] is None
        assert games[0]["venue_state"] is None

    def test_sets_venue_city_and_state_from_the_home_teams_own_venue(self):
        # CFBD's own /games response has no city/state breakdown, only a
        # bare venue name string -- the home team's own /teams `location`
        # is the same underlying Venue object, confirmed live via CFBD's
        # OpenAPI schema, so this is sourced from the same already-cached
        # teams call venue_indoor itself already uses, not a second lookup.
        mock_s3 = _make_s3()
        client = MagicMock()
        client.get_teams.return_value = [
            _team("2", "Georgia", dome=False, city="Athens", state="GA"),
            _team("52", "Alabama", dome=False, city="Tuscaloosa", state="AL"),
        ]
        games = [{"id": "1", "homeId": "2", "awayId": "52"}]

        attach_venue_indoor(games, 2025, client, mock_s3, BUCKET)

        assert games[0]["venue_city"] == "Athens"
        assert games[0]["venue_state"] == "GA"

    def test_fetches_teams_at_most_once_across_many_games(self):
        mock_s3 = _make_s3()
        client = MagicMock()
        client.get_teams.return_value = [_team("2", "Georgia"), _team("52", "Alabama")]
        games = [
            {"id": "1", "homeId": "2", "awayId": "52"},
            {"id": "2", "homeId": "52", "awayId": "2"},
        ]

        attach_venue_indoor(games, 2025, client, mock_s3, BUCKET)

        client.get_teams.assert_called_once_with(2025)
