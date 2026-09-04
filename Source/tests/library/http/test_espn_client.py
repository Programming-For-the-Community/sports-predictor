"""
Unit tests for library.http.espn -- the ESPN_API_ROOT_URL/ESPN_USER_AGENT
env-var override resolution, EspnBaseClient's constructor wiring, and
espn_scoreboard_date's UTC-to-Eastern bucketing.
"""
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from library.http.espn import (
    DEFAULT_ESPN_API_ROOT_URL,
    DEFAULT_ESPN_USER_AGENT,
    EspnBaseClient,
    _espn_root_url,
    _espn_user_agent,
    espn_scoreboard_date,
)


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
    def test_defaults_when_env_var_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ESPN_USER_AGENT", None)
            assert _espn_user_agent() == DEFAULT_ESPN_USER_AGENT

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

    def test_passes_default_user_agent_when_env_var_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ESPN_USER_AGENT", None)
            with patch("library.http.espn.HttpClient.__init__", return_value=None) as mock_init:
                EspnBaseClient(sport_path="football/nfl")

        assert mock_init.call_args.kwargs["user_agent"] == DEFAULT_ESPN_USER_AGENT


class TestEspnScoreboardDate:
    def test_daytime_utc_matches_utc_date(self):
        # Midday UTC is well inside the same Eastern calendar date.
        assert espn_scoreboard_date(datetime(2026, 9, 3, 18, 0, tzinfo=timezone.utc)) == "20260903"

    def test_early_utc_morning_is_still_the_previous_eastern_date(self):
        # 01:00 UTC is 9pm Eastern the day before -- a live game that
        # kicked off in Eastern-evening prime time is still being played
        # under ESPN's *previous* UTC-date scoreboard bucket.
        assert espn_scoreboard_date(datetime(2026, 9, 4, 1, 0, tzinfo=timezone.utc)) == "20260903"

    def test_late_utc_evening_matches_utc_date(self):
        # 23:00 UTC is 7pm Eastern the same day.
        assert espn_scoreboard_date(datetime(2026, 9, 3, 23, 0, tzinfo=timezone.utc)) == "20260903"
