"""
Unit tests for the NFL ingest Lambda's small pure-function helpers:
_most_recent_sunday (the date-math that makes the Tuesday run and the
Wednesday retry agree on the same week), _object_exists/_put_json (S3
plumbing), and _all_team_ids (the league-wide team list every unconditional
fetch -- rosters, depth charts -- is sourced from).

The nfl_ingest module is registered in sys.modules by conftest.py, which
also sets RAW_BUCKET_NAME before the module is imported (it's read at
module level by the handler).
"""
from datetime import date
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

import nfl_ingest

from _ingest_test_helpers import teams_response


class TestMostRecentSunday:
    SUNDAY = date(2026, 9, 13)

    def test_sunday_itself_returns_same_day(self):
        assert nfl_ingest._most_recent_sunday(self.SUNDAY) == "20260913"

    def test_monday_returns_previous_day(self):
        assert nfl_ingest._most_recent_sunday(date(2026, 9, 14)) == "20260913"

    def test_tuesday_returns_same_sunday_as_wednesday(self):
        tuesday = nfl_ingest._most_recent_sunday(date(2026, 9, 15))
        wednesday = nfl_ingest._most_recent_sunday(date(2026, 9, 16))

        assert tuesday == "20260913"
        assert wednesday == "20260913"
        assert tuesday == wednesday  # the whole point of this function

    def test_saturday_does_not_roll_forward_to_next_sunday(self):
        assert nfl_ingest._most_recent_sunday(date(2026, 9, 19)) == "20260913"

    def test_defaults_to_actual_today_when_not_given(self):
        # Just confirms the default path runs and returns the expected
        # format -- the date-math itself is covered by the fixed-date
        # cases above.
        result = nfl_ingest._most_recent_sunday()
        assert len(result) == 8
        assert result.isdigit()


class TestIngestHelpers:
    def test_object_exists_returns_true_when_key_found(self):
        mock_s3 = MagicMock()
        mock_s3.head_object.return_value = {"ContentLength": 100}

        with patch.object(nfl_ingest, "_s3", mock_s3):
            assert nfl_ingest._object_exists("some/key.json") is True

    def test_object_exists_returns_false_on_client_error(self):
        mock_s3 = MagicMock()
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
        )

        with patch.object(nfl_ingest, "_s3", mock_s3):
            assert nfl_ingest._object_exists("missing/key.json") is False

    def test_put_json_serialises_payload_correctly(self):
        mock_s3 = MagicMock()
        payload = {"events": [{"id": "123"}]}

        with patch.object(nfl_ingest, "_s3", mock_s3):
            nfl_ingest._put_json("nfl/test.json", payload)

        call_kwargs = mock_s3.put_object.call_args.kwargs
        assert call_kwargs["Key"] == "nfl/test.json"
        assert call_kwargs["ContentType"] == "application/json"
        assert '"events"' in call_kwargs["Body"]


class TestAllTeamIds:
    def test_extracts_every_team_id_from_the_real_response_shape(self):
        client = MagicMock()
        client.get_teams.return_value = teams_response("12", "13", "14")

        assert nfl_ingest._all_team_ids(client) == ["12", "13", "14"]

    def test_empty_leagues_returns_no_ids(self):
        client = MagicMock()
        client.get_teams.return_value = {"sports": [{"leagues": []}]}

        assert nfl_ingest._all_team_ids(client) == []
