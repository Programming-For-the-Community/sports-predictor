"""
Unit tests for library.http.espn -- the ESPN_API_ROOT_URL/ESPN_USER_AGENT
env-var override resolution and EspnBaseClient's constructor wiring.
"""
import os
from unittest.mock import MagicMock, patch

from library.http.espn import DEFAULT_ESPN_API_ROOT_URL, EspnBaseClient, _espn_root_url, _espn_user_agent


class TestEspnRootUrl:
    def test_defaults_when_env_var_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ESPN_API_ROOT_URL", None)
            assert _espn_root_url() == DEFAULT_ESPN_API_ROOT_URL

    def test_uses_env_var_override_when_set(self):
        with patch.dict(os.environ, {"ESPN_API_ROOT_URL": "https://site.web.api.espn.com/apis/site/v2/sports"}):
            assert _espn_root_url() == "https://site.web.api.espn.com/apis/site/v2/sports"

    def test_strips_trailing_slash(self):
        with patch.dict(os.environ, {"ESPN_API_ROOT_URL": "https://example.com/root/"}):
            assert _espn_root_url() == "https://example.com/root"


class TestEspnUserAgent:
    def test_none_when_env_var_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ESPN_USER_AGENT", None)
            assert _espn_user_agent() is None

    def test_uses_env_var_override_when_set(self):
        with patch.dict(os.environ, {"ESPN_USER_AGENT": "python-requests/2.31.0"}):
            assert _espn_user_agent() == "python-requests/2.31.0"


class TestEspnBaseClient:
    def test_passes_resolved_root_url_and_user_agent_to_http_client(self):
        with patch.dict(os.environ, {
            "ESPN_API_ROOT_URL": "https://site.web.api.espn.com/apis/site/v2/sports",
            "ESPN_USER_AGENT": "python-requests/2.31.0",
        }), patch("library.http.espn.HttpClient.__init__", return_value=None) as mock_init:
            EspnBaseClient(sport_path="football/nfl")

        mock_init.assert_called_once_with(
            base_url="https://site.web.api.espn.com/apis/site/v2/sports/football/nfl",
            min_interval_seconds=0.3,
            user_agent="python-requests/2.31.0",
        )

    def test_passes_none_user_agent_when_env_var_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ESPN_USER_AGENT", None)
            with patch("library.http.espn.HttpClient.__init__", return_value=None) as mock_init:
                EspnBaseClient(sport_path="football/nfl")

        assert mock_init.call_args.kwargs["user_agent"] is None
