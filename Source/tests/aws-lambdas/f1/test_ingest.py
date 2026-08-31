"""
Unit tests for the F1 ingest Lambda handler: date/season resolution, the
trailing-window race discovery, per-round results/qualifying/sprint/
pitstops fetch-and-write, per-round failure isolation, and the
standings snapshot (only captured when a round was actually processed).
All AWS and Jolpica calls are mocked.

The f1_ingest module is registered in sys.modules by conftest.py.
"""
from datetime import date
from unittest.mock import MagicMock, patch

import f1_ingest


def _schedule(races):
    return {"MRData": {"RaceTable": {"Races": races}}}


def _race(round_="1", race_date="2026-08-23"):
    return {"round": round_, "date": race_date, "raceName": "Test GP"}


def _empty_sprint():
    return {"MRData": {"RaceTable": {"Races": []}}}


def _client(races=None):
    client = MagicMock()
    client.get_races.return_value = _schedule(races or [])
    client.get_race_results.return_value = {"MRData": {"RaceTable": {"Races": [{"Results": [{"position": "1"}]}]}}}
    client.get_qualifying.return_value = {"MRData": {"RaceTable": {"Races": [{"QualifyingResults": []}]}}}
    client.get_sprint.return_value = _empty_sprint()
    client.get_pitstops.return_value = {"MRData": {"RaceTable": {"Races": [{"PitStops": []}]}}}
    client.get_driver_standings.return_value = {"MRData": {"StandingsTable": {}}}
    client.get_constructor_standings.return_value = {"MRData": {"StandingsTable": {}}}
    return client


class TestLambdaHandlerDateResolution:
    def test_uses_explicit_date_and_season_override(self):
        mock_client = _client()

        with patch.object(f1_ingest, "JolpicaClient", return_value=mock_client), \
             patch.object(f1_ingest, "_s3", MagicMock()):
            f1_ingest.lambda_handler({"date": "20260823", "season": 2026}, None)

        mock_client.get_races.assert_called_once_with(2026)

    def test_defaults_to_today_and_todays_year_when_nothing_given(self):
        mock_client = _client()

        with patch.object(f1_ingest, "JolpicaClient", return_value=mock_client), \
             patch.object(f1_ingest, "_s3", MagicMock()), \
             patch("f1_ingest.date") as mock_date:
            mock_date.today.return_value = date(2026, 8, 23)
            f1_ingest.lambda_handler({}, None)

        mock_client.get_races.assert_called_once_with(2026)


class TestScheduleSnapshot:
    def test_writes_the_full_calendar_every_run_unconditionally(self):
        mock_client = _client(races=[])  # nothing in the trailing window
        mock_s3 = MagicMock()

        with patch.object(f1_ingest, "JolpicaClient", return_value=mock_client), \
             patch.object(f1_ingest, "_s3", mock_s3), \
             patch("f1_ingest.date") as mock_date:
            mock_date.today.return_value = date(2026, 8, 23)
            f1_ingest.lambda_handler({}, None)

        written_keys = [call.kwargs["Key"] for call in mock_s3.put_object.call_args_list]
        assert "f1/schedule/2026/20260823.json" in written_keys

    def test_zero_extra_jolpica_requests_for_the_schedule_write(self):
        """get_races is called exactly once -- the same response already
        used for trailing-window discovery is reused for the schedule
        write, not re-fetched."""
        mock_client = _client(races=[_race()])
        mock_s3 = MagicMock()

        with patch.object(f1_ingest, "JolpicaClient", return_value=mock_client), \
             patch.object(f1_ingest, "_s3", mock_s3), \
             patch("f1_ingest.date") as mock_date:
            mock_date.today.return_value = date(2026, 8, 23)
            f1_ingest.lambda_handler({}, None)

        mock_client.get_races.assert_called_once()


class TestRaceDiscoveryAndFetch:
    def test_no_races_in_the_trailing_window_writes_no_round_specific_files(self):
        mock_client = _client(races=[_race(race_date="2026-01-01")])  # far outside the window
        mock_s3 = MagicMock()

        with patch.object(f1_ingest, "JolpicaClient", return_value=mock_client), \
             patch.object(f1_ingest, "_s3", mock_s3):
            result = f1_ingest.lambda_handler({"date": "20260823"}, None)

        assert result == {"processed": 0, "failed": 0, "standings_captured": False}
        # The season's own full-calendar schedule snapshot is still
        # written unconditionally every run (TestScheduleSnapshot) -- only
        # per-round results/qualifying/sprint/pitstops writes are gated on
        # the trailing window.
        written_keys = [call.kwargs["Key"] for call in mock_s3.put_object.call_args_list]
        assert written_keys == ["f1/schedule/2026/20260823.json"]

    def test_a_race_on_target_date_is_fetched_and_written(self):
        mock_client = _client(races=[_race(round_="14", race_date="2026-08-23")])
        mock_s3 = MagicMock()

        with patch.object(f1_ingest, "JolpicaClient", return_value=mock_client), \
             patch.object(f1_ingest, "_s3", mock_s3):
            result = f1_ingest.lambda_handler({"date": "20260823", "season": 2026}, None)

        assert result["processed"] == 1
        assert result["failed"] == 0
        mock_client.get_race_results.assert_called_once_with(2026, 14)
        mock_client.get_qualifying.assert_called_once_with(2026, 14)
        written_keys = [c.kwargs["Key"] for c in mock_s3.put_object.call_args_list]
        assert "f1/results/2026/14.json" in written_keys
        assert "f1/qualifying/2026/14.json" in written_keys

    def test_a_race_a_couple_days_before_target_date_is_still_in_window(self):
        mock_client = _client(races=[_race(round_="14", race_date="2026-08-21")])  # 2 days before

        with patch.object(f1_ingest, "JolpicaClient", return_value=mock_client), \
             patch.object(f1_ingest, "_s3", MagicMock()):
            result = f1_ingest.lambda_handler({"date": "20260823", "season": 2026}, None)

        assert result["processed"] == 1

    def test_a_race_more_than_3_days_before_target_date_is_out_of_window(self):
        mock_client = _client(races=[_race(round_="14", race_date="2026-08-10")])

        with patch.object(f1_ingest, "JolpicaClient", return_value=mock_client), \
             patch.object(f1_ingest, "_s3", MagicMock()):
            result = f1_ingest.lambda_handler({"date": "20260823", "season": 2026}, None)

        assert result["processed"] == 0

    def test_pitstops_only_fetched_for_2011_and_later(self):
        mock_client = _client(races=[_race(round_="1", race_date="2010-08-23")])
        mock_s3 = MagicMock()

        with patch.object(f1_ingest, "JolpicaClient", return_value=mock_client), \
             patch.object(f1_ingest, "_s3", mock_s3):
            f1_ingest.lambda_handler({"date": "20100823", "season": 2010}, None)

        mock_client.get_pitstops.assert_not_called()

    def test_a_sprint_weekend_with_real_results_writes_the_sprint_file(self):
        mock_client = _client(races=[_race(round_="5", race_date="2026-08-23")])
        mock_client.get_sprint.return_value = {
            "MRData": {"RaceTable": {"Races": [{"SprintResults": [{"position": "1"}]}]}},
        }
        mock_s3 = MagicMock()

        with patch.object(f1_ingest, "JolpicaClient", return_value=mock_client), \
             patch.object(f1_ingest, "_s3", mock_s3):
            f1_ingest.lambda_handler({"date": "20260823", "season": 2026}, None)

        written_keys = [c.kwargs["Key"] for c in mock_s3.put_object.call_args_list]
        assert "f1/sprint/2026/5.json" in written_keys

    def test_a_non_sprint_weekend_does_not_write_a_sprint_file(self):
        mock_client = _client(races=[_race(round_="1", race_date="2026-08-23")])  # get_sprint defaults to empty
        mock_s3 = MagicMock()

        with patch.object(f1_ingest, "JolpicaClient", return_value=mock_client), \
             patch.object(f1_ingest, "_s3", mock_s3):
            f1_ingest.lambda_handler({"date": "20260823", "season": 2026}, None)

        written_keys = [c.kwargs["Key"] for c in mock_s3.put_object.call_args_list]
        assert not any("/sprint/" in k for k in written_keys)

    def test_one_rounds_fetch_failure_does_not_block_the_others(self):
        mock_client = _client(races=[_race(round_="1", race_date="2026-08-22"), _race(round_="2", race_date="2026-08-23")])
        mock_client.get_race_results.side_effect = [Exception("network error"), {"MRData": {"RaceTable": {"Races": [{"Results": []}]}}}]

        with patch.object(f1_ingest, "JolpicaClient", return_value=mock_client), \
             patch.object(f1_ingest, "_s3", MagicMock()):
            result = f1_ingest.lambda_handler({"date": "20260823", "season": 2026}, None)

        assert result["processed"] == 1
        assert result["failed"] == 1


class TestStandingsSnapshot:
    def test_captures_standings_as_of_the_latest_processed_round(self):
        mock_client = _client(races=[_race(round_="14", race_date="2026-08-23")])
        mock_s3 = MagicMock()

        with patch.object(f1_ingest, "JolpicaClient", return_value=mock_client), \
             patch.object(f1_ingest, "_s3", mock_s3):
            result = f1_ingest.lambda_handler({"date": "20260823", "season": 2026}, None)

        assert result["standings_captured"] is True
        mock_client.get_driver_standings.assert_called_once_with(2026, 14)
        mock_client.get_constructor_standings.assert_called_once_with(2026, 14)
        written_keys = [c.kwargs["Key"] for c in mock_s3.put_object.call_args_list]
        assert "f1/standings/2026/20260823.json" in written_keys

    def test_no_rounds_processed_means_no_standings_snapshot(self):
        mock_client = _client(races=[])

        with patch.object(f1_ingest, "JolpicaClient", return_value=mock_client), \
             patch.object(f1_ingest, "_s3", MagicMock()):
            result = f1_ingest.lambda_handler({"date": "20260823", "season": 2026}, None)

        assert result["standings_captured"] is False
        mock_client.get_driver_standings.assert_not_called()

    def test_a_failed_standings_fetch_does_not_block_the_rounds_result(self):
        mock_client = _client(races=[_race(round_="14", race_date="2026-08-23")])
        mock_client.get_driver_standings.side_effect = Exception("network error")

        with patch.object(f1_ingest, "JolpicaClient", return_value=mock_client), \
             patch.object(f1_ingest, "_s3", MagicMock()):
            result = f1_ingest.lambda_handler({"date": "20260823", "season": 2026}, None)

        assert result["standings_captured"] is False
        assert result["processed"] == 1
