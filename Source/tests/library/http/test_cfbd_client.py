"""
Unit tests for library.http.cfbd -- CFBD_API_ROOT_URL env-var override
resolution, Secrets Manager key resolution/caching (this project's first
use of Secrets Manager -- see project plan), and each endpoint method's
query-param construction. get_* methods are tested via _get mocked on
the instance, not real HTTP -- what matters here is param shaping, not
HttpClient's own retry/rate-limit behavior (already covered elsewhere).
"""
import json
import os
from unittest.mock import MagicMock, patch

import pytest

import library.http.cfbd as cfbd
from library.http.cfbd import DEFAULT_CFBD_API_ROOT_URL, CFBDClient, _cfbd_root_url, _resolve_api_key


@pytest.fixture(autouse=True)
def reset_api_key_cache():
    """_cached_api_key/_secrets_client are module-level so a warm
    container only ever calls Secrets Manager once -- tests need a clean
    slate each time or an earlier test's cached key would leak in."""
    cfbd._cached_api_key = None
    cfbd._secrets_client = None
    yield
    cfbd._cached_api_key = None
    cfbd._secrets_client = None


def _secret_response(**fields) -> dict:
    return {"SecretString": json.dumps(fields)}


def _make_client(api_key: str = "ingest-key-123") -> CFBDClient:
    with patch.dict(os.environ, {
        "CFBD_API_KEY_SECRET_ARN": "arn:aws:secretsmanager:us-east-2:123:secret:dev",
        "CFBD_API_KEY_SECRET_FIELD": "ncaa_fb_ingest_key",
    }), patch("boto3.client", return_value=MagicMock(
        get_secret_value=MagicMock(return_value=_secret_response(ncaa_fb_ingest_key=api_key)),
    )):
        return CFBDClient()


def _client_with_mocked_get() -> CFBDClient:
    client = _make_client()
    client._get = MagicMock(return_value=[])
    return client


class TestCfbdRootUrl:
    def test_defaults_when_env_var_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CFBD_API_ROOT_URL", None)
            assert _cfbd_root_url() == DEFAULT_CFBD_API_ROOT_URL

    def test_uses_env_var_override_when_set(self):
        with patch.dict(os.environ, {"CFBD_API_ROOT_URL": "https://example.com/cfbd"}):
            assert _cfbd_root_url() == "https://example.com/cfbd"

    def test_strips_trailing_slash(self):
        with patch.dict(os.environ, {"CFBD_API_ROOT_URL": "https://example.com/cfbd/"}):
            assert _cfbd_root_url() == "https://example.com/cfbd"


class TestResolveApiKey:
    def test_fetches_from_secrets_manager_and_selects_field(self):
        mock_secrets = MagicMock()
        mock_secrets.get_secret_value.return_value = _secret_response(
            ncaa_fb_ingest_key="ingest-key-123", ncaa_fb_backfill_key="backfill-key-456",
        )

        with patch.dict(os.environ, {
            "CFBD_API_KEY_SECRET_ARN": "arn:aws:secretsmanager:us-east-2:123:secret:dev",
            "CFBD_API_KEY_SECRET_FIELD": "ncaa_fb_ingest_key",
        }), patch("boto3.client", return_value=mock_secrets):
            key = _resolve_api_key()

        assert key == "ingest-key-123"
        mock_secrets.get_secret_value.assert_called_once_with(SecretId="arn:aws:secretsmanager:us-east-2:123:secret:dev")

    def test_selects_backfill_field_when_configured(self):
        mock_secrets = MagicMock()
        mock_secrets.get_secret_value.return_value = _secret_response(
            ncaa_fb_ingest_key="ingest-key-123", ncaa_fb_backfill_key="backfill-key-456",
        )

        with patch.dict(os.environ, {
            "CFBD_API_KEY_SECRET_ARN": "arn:aws:secretsmanager:us-east-2:123:secret:dev",
            "CFBD_API_KEY_SECRET_FIELD": "ncaa_fb_backfill_key",
        }), patch("boto3.client", return_value=mock_secrets):
            key = _resolve_api_key()

        assert key == "backfill-key-456"

    def test_second_call_is_a_cache_hit_not_a_second_secrets_manager_call(self):
        mock_secrets = MagicMock()
        mock_secrets.get_secret_value.return_value = _secret_response(ncaa_fb_ingest_key="ingest-key-123")

        with patch.dict(os.environ, {
            "CFBD_API_KEY_SECRET_ARN": "arn:aws:secretsmanager:us-east-2:123:secret:dev",
            "CFBD_API_KEY_SECRET_FIELD": "ncaa_fb_ingest_key",
        }), patch("boto3.client", return_value=mock_secrets):
            _resolve_api_key()
            _resolve_api_key()

        mock_secrets.get_secret_value.assert_called_once()


class TestCFBDClientConstructor:
    def test_sets_bearer_authorization_header(self):
        client = _make_client(api_key="ingest-key-123")
        assert client._session.headers["Authorization"] == "Bearer ingest-key-123"


class TestGetGames:
    def test_scopes_to_fbs_and_regular_season_by_default(self):
        client = _client_with_mocked_get()
        client.get_games(2025, week=4)
        client._get.assert_called_once_with("games", params={
            "year": 2025, "seasonType": "regular", "classification": "fbs", "week": 4,
        })

    def test_week_omitted_fetches_whole_season(self):
        client = _client_with_mocked_get()
        client.get_games(2025)
        client._get.assert_called_once_with("games", params={
            "year": 2025, "seasonType": "regular", "classification": "fbs",
        })

    def test_postseason_override(self):
        client = _client_with_mocked_get()
        client.get_games(2025, week=1, season_type="postseason")
        assert client._get.call_args.kwargs["params"]["seasonType"] == "postseason"


class TestGetGamePlayerStats:
    def test_scopes_to_year_week_season_type(self):
        client = _client_with_mocked_get()
        client.get_game_player_stats(2025, 4, "regular")
        client._get.assert_called_once_with("games/players", params={
            "year": 2025, "week": 4, "seasonType": "regular", "classification": "fbs",
        })


class TestGetGameTeamStats:
    def test_scopes_to_year_week_season_type(self):
        client = _client_with_mocked_get()
        client.get_game_team_stats(2025, 4, "regular")
        client._get.assert_called_once_with("games/teams", params={
            "year": 2025, "week": 4, "seasonType": "regular", "classification": "fbs",
        })


class TestGetCoaches:
    def test_scopes_to_year_only_no_team_filter(self):
        client = _client_with_mocked_get()
        client.get_coaches(2025)
        client._get.assert_called_once_with("coaches", params={"year": 2025})


class TestGetRankings:
    def test_week_included_when_given(self):
        client = _client_with_mocked_get()
        client.get_rankings(2025, week=10)
        client._get.assert_called_once_with("rankings", params={"year": 2025, "seasonType": "regular", "week": 10})

    def test_week_omitted_when_not_given(self):
        client = _client_with_mocked_get()
        client.get_rankings(2025)
        client._get.assert_called_once_with("rankings", params={"year": 2025, "seasonType": "regular"})


class TestGetTeams:
    def test_scopes_to_year_only(self):
        client = _client_with_mocked_get()
        client.get_teams(2025)
        client._get.assert_called_once_with("teams", params={"year": 2025})


class TestGetCalendar:
    def test_scopes_to_year_only(self):
        client = _client_with_mocked_get()
        client.get_calendar(2025)
        client._get.assert_called_once_with("calendar", params={"year": 2025})


class TestGetRoster:
    def test_scopes_to_year_only(self):
        client = _client_with_mocked_get()
        client.get_roster(2025)
        client._get.assert_called_once_with("roster", params={"year": 2025})
