"""
Unit tests for library.http.f1 -- JolpicaClient's endpoint wiring and its
own paginating get_laps. Base-URL/rate-limit wiring only (HttpClient's
retry/pacing behavior itself is exercised by test_client.py, not
re-tested here).
"""
from unittest.mock import MagicMock, patch

from library.http.f1 import (
    DEFAULT_JOLPICA_API_ROOT_URL,
    DEFAULT_JOLPICA_USER_AGENT,
    DEFAULT_MIN_INTERVAL_SECONDS,
    PAGE_SIZE,
    JolpicaClient,
)


class TestJolpicaClientInit:
    def test_uses_the_jolpica_base_url_and_the_default_user_agent_with_no_env_override(self, monkeypatch):
        monkeypatch.delenv("JOLPICA_API_ROOT_URL", raising=False)
        monkeypatch.delenv("JOLPICA_USER_AGENT", raising=False)
        with patch("library.http.f1.HttpClient.__init__", return_value=None) as mock_init:
            JolpicaClient()

        assert mock_init.call_args.kwargs["base_url"] == DEFAULT_JOLPICA_API_ROOT_URL
        assert mock_init.call_args.kwargs["user_agent"] == DEFAULT_JOLPICA_USER_AGENT

    def test_env_vars_override_the_root_url_and_user_agent(self, monkeypatch):
        monkeypatch.setenv("JOLPICA_API_ROOT_URL", "https://example.test/ergast/f1/")
        monkeypatch.setenv("JOLPICA_USER_AGENT", "custom-ua/2.0")
        with patch("library.http.f1.HttpClient.__init__", return_value=None) as mock_init:
            JolpicaClient()

        assert mock_init.call_args.kwargs["base_url"] == "https://example.test/ergast/f1"
        assert mock_init.call_args.kwargs["user_agent"] == "custom-ua/2.0"

    def test_default_min_interval_matches_the_stricter_sustained_rate_bound(self):
        assert DEFAULT_MIN_INTERVAL_SECONDS == 3600 / 500


class TestJolpicaClientEndpoints:
    def test_get_race_results_passes_season_round_path_and_page_size(self):
        client = JolpicaClient.__new__(JolpicaClient)
        client._get = MagicMock(return_value={"MRData": {}})

        result = client.get_race_results(2024, 1)

        client._get.assert_called_once_with("2024/1/results.json", params={"limit": PAGE_SIZE})
        assert result == {"MRData": {}}

    def test_get_qualifying_passes_season_round_path(self):
        client = JolpicaClient.__new__(JolpicaClient)
        client._get = MagicMock(return_value={"MRData": {}})

        client.get_qualifying(2024, 1)

        client._get.assert_called_once_with("2024/1/qualifying.json", params={"limit": PAGE_SIZE})

    def test_get_sprint_passes_season_round_path(self):
        client = JolpicaClient.__new__(JolpicaClient)
        client._get = MagicMock(return_value={"MRData": {}})

        client.get_sprint(2024, 5)

        client._get.assert_called_once_with("2024/5/sprint.json", params={"limit": PAGE_SIZE})

    def test_get_pitstops_passes_season_round_path(self):
        client = JolpicaClient.__new__(JolpicaClient)
        client._get = MagicMock(return_value={"MRData": {}})

        client.get_pitstops(2024, 1)

        client._get.assert_called_once_with("2024/1/pitstops.json", params={"limit": PAGE_SIZE})

    def test_get_driver_standings_passes_season_round_path(self):
        client = JolpicaClient.__new__(JolpicaClient)
        client._get = MagicMock(return_value={"MRData": {}})

        client.get_driver_standings(2024, 1)

        client._get.assert_called_once_with("2024/1/driverstandings.json", params={"limit": PAGE_SIZE})

    def test_get_constructor_standings_passes_season_round_path(self):
        client = JolpicaClient.__new__(JolpicaClient)
        client._get = MagicMock(return_value={"MRData": {}})

        client.get_constructor_standings(2024, 1)

        client._get.assert_called_once_with("2024/1/constructorstandings.json", params={"limit": PAGE_SIZE})

    def test_get_races_passes_season_only_path(self):
        client = JolpicaClient.__new__(JolpicaClient)
        client._get = MagicMock(return_value={"MRData": {}})

        client.get_races(2024)

        client._get.assert_called_once_with("2024/races.json", params={"limit": PAGE_SIZE})


class TestGetLaps:
    def test_a_single_page_under_the_page_size_returns_that_pages_laps_and_stops(self):
        client = JolpicaClient.__new__(JolpicaClient)
        client._get = MagicMock(return_value={
            "MRData": {
                "total": "2",
                "RaceTable": {"Races": [{"Laps": [{"number": "1"}, {"number": "2"}]}]},
            },
        })

        laps = client.get_laps(2024, 1)

        assert laps == [{"number": "1"}, {"number": "2"}]
        client._get.assert_called_once_with("2024/1/laps.json", params={"limit": PAGE_SIZE, "offset": 0})

    def test_multiple_pages_are_walked_by_offset_until_total_is_reached(self):
        client = JolpicaClient.__new__(JolpicaClient)
        page_one = {"MRData": {"total": str(PAGE_SIZE + 1), "RaceTable": {"Races": [{"Laps": [{"n": i} for i in range(PAGE_SIZE)]}]}}}
        page_two = {"MRData": {"total": str(PAGE_SIZE + 1), "RaceTable": {"Races": [{"Laps": [{"n": PAGE_SIZE}]}]}}}
        client._get = MagicMock(side_effect=[page_one, page_two])

        laps = client.get_laps(2024, 1)

        assert len(laps) == PAGE_SIZE + 1
        assert client._get.call_count == 2
        client._get.assert_any_call("2024/1/laps.json", params={"limit": PAGE_SIZE, "offset": 0})
        client._get.assert_any_call("2024/1/laps.json", params={"limit": PAGE_SIZE, "offset": PAGE_SIZE})

    def test_an_empty_page_stops_pagination_even_if_total_claims_more(self):
        client = JolpicaClient.__new__(JolpicaClient)
        client._get = MagicMock(return_value={"MRData": {"total": "999", "RaceTable": {"Races": []}}})

        laps = client.get_laps(2024, 1)

        assert laps == []
        client._get.assert_called_once()
