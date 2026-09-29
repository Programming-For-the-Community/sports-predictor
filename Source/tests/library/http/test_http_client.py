"""
Unit tests for library.http.client.HttpClient's request path -- URL
building, parameter passing, retry-then-succeed, and giving up.
"""
from unittest.mock import MagicMock, patch

import pytest
import requests

from library.http import client as client_module
from library.http.client import MAX_RETRIES, REQUEST_TIMEOUT_SECONDS, HttpClient


def _response(payload=None, error: Exception | None = None):
    response = MagicMock()
    response.json.return_value = payload
    if error is not None:
        response.raise_for_status.side_effect = error
    return response


@pytest.fixture
def http():
    client = HttpClient("https://api.example.com/v1/", min_interval_seconds=0)
    client._session = MagicMock()
    with patch.object(client_module.time, "sleep"):
        yield client


class TestGet:
    def test_joins_base_url_and_path_and_returns_json(self, http):
        http._session.get.return_value = _response({"ok": True})

        assert http._get("/scoreboard", {"dates": "20260101"}) == {"ok": True}
        http._session.get.assert_called_once_with(
            "https://api.example.com/v1/scoreboard", params={"dates": "20260101"}, timeout=REQUEST_TIMEOUT_SECONDS,
        )


class TestGetAbsolute:
    def test_requests_the_url_as_given_with_empty_params_by_default(self, http):
        http._session.get.return_value = _response({"items": []})

        assert http.get_absolute("https://other.example.com/ref/1") == {"items": []}
        http._session.get.assert_called_once_with(
            "https://other.example.com/ref/1", params={}, timeout=REQUEST_TIMEOUT_SECONDS,
        )


class TestRetries:
    def test_retries_a_failed_request_then_returns_the_success(self, http):
        http._session.get.side_effect = [
            _response(error=requests.HTTPError("503")),
            requests.ConnectionError("reset"),
            _response({"ok": 1}),
        ]

        assert http._get("x", {}) == {"ok": 1}
        assert http._session.get.call_count == 3

    def test_raises_after_max_retries(self, http):
        http._session.get.side_effect = requests.Timeout("slow")

        with pytest.raises(RuntimeError, match=f"failed after {MAX_RETRIES} attempts"):
            http._get("x", {})
        assert http._session.get.call_count == MAX_RETRIES


class TestSessionHeaders:
    def test_defaults_to_a_browser_user_agent(self):
        assert "Mozilla/5.0" in HttpClient("https://a")._session.headers["User-Agent"]

    def test_custom_user_agent_overrides_the_default(self):
        assert HttpClient("https://a", user_agent="custom/1")._session.headers["User-Agent"] == "custom/1"
