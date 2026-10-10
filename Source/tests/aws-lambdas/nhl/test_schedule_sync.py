"""
Unit tests for the NHL schedule-sync Lambda: which dates are fetched,
skipped, refreshed and written. All AWS and ESPN calls are mocked.

The nhl_schedule_sync module is registered in sys.modules by conftest.py.
"""
from datetime import date
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

import nhl_schedule_sync

TODAY = date(2026, 10, 9)


def _scoreboard(season_type=2, home_id="13", away_id="1"):
    return {"events": [{
        "id": "1", "season": {"year": 2027, "type": season_type},
        "competitions": [{"competitors": [{"team": {"id": home_id}}, {"team": {"id": away_id}}]}],
    }]}


def _run(client, written=frozenset()):
    s3 = MagicMock()

    def head_object(Bucket, Key, ExpectedBucketOwner):
        if Key not in written:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")

    s3.head_object.side_effect = head_object
    with patch.object(nhl_schedule_sync, "NHLClient", return_value=client), \
         patch.object(nhl_schedule_sync, "_s3", s3), \
         patch.object(nhl_schedule_sync, "date") as mock_date, \
         patch.object(nhl_schedule_sync, "SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS", 20):
        mock_date.today.return_value = TODAY
        result = nhl_schedule_sync.lambda_handler({}, None)
    return result, [call.kwargs["Key"] for call in s3.put_object.call_args_list]


def test_every_unwritten_date_with_a_real_game_is_written_under_the_ingest_key():
    client = MagicMock()
    client.get_scoreboard_for_date.return_value = _scoreboard()

    result, keys = _run(client)

    assert result == {"synced": 20, "refreshed": 0, "skipped": 0, "failed": 0}
    assert keys[0] == "nhl/scoreboard/20261009.json"
    assert keys[-1] == "nhl/scoreboard/20261028.json"


def test_already_written_dates_are_refreshed_inside_the_window_and_skipped_beyond_it():
    client = MagicMock()
    client.get_scoreboard_for_date.return_value = _scoreboard()
    written = {"nhl/scoreboard/20261010.json", "nhl/scoreboard/20261025.json"}

    result, keys = _run(client, written)

    assert (result["refreshed"], result["skipped"], result["synced"]) == (1, 1, 18)
    assert "nhl/scoreboard/20261010.json" in keys           # day 2: inside the 14-day refresh window
    assert "nhl/scoreboard/20261025.json" not in keys       # day 17: beyond it
    assert client.get_scoreboard_for_date.call_count == 19


def test_dates_with_nothing_to_ingest_are_never_written():
    client = MagicMock()
    client.get_scoreboard_for_date.side_effect = (
        [{"events": []}, _scoreboard(season_type=1), _scoreboard(home_id="129030", away_id="129031")] + [_scoreboard()] * 17
    )

    result, keys = _run(client)

    assert (result["skipped"], result["synced"]) == (3, 17)
    assert keys[0] == "nhl/scoreboard/20261012.json"


def test_one_failing_date_does_not_stop_the_rest():
    client = MagicMock()
    client.get_scoreboard_for_date.side_effect = [RuntimeError("boom")] + [_scoreboard()] * 19

    result, _ = _run(client)

    assert (result["failed"], result["synced"]) == (1, 19)
