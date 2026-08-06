"""
Unit tests for migrations/backfill_kickoff_time.py. S3Manager/DynamoDBTable
are mocked throughout -- these verify the migration's own logic, not real
AWS behavior.
"""
from unittest.mock import MagicMock, patch

import backfill_kickoff_time as migration


def _scoreboard_event(event_id="401547417", home_id="12", away_id="24"):
    return {
        "id": event_id,
        "date": "2025-09-28T20:25Z",
        "status": {"type": {"completed": True}},
        "season": {"year": 2025, "type": 2},
        "week": {"number": 4},
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"id": home_id}, "score": "27", "winner": True},
                {"homeAway": "away", "team": {"id": away_id}, "score": "20", "winner": False},
            ],
        }],
    }


class TestMigrate:
    def test_upserts_every_event_with_kickoff_time(self):
        mock_s3 = MagicMock()
        mock_s3.list_keys.return_value = ["nfl/scoreboard/2025/2/4.json"]
        mock_s3.get_json.return_value = {"events": [_scoreboard_event()]}
        mock_events_table = MagicMock()

        migration.migrate(mock_s3, mock_events_table, dry_run=False)

        mock_events_table.put_item.assert_called_once()
        written = mock_events_table.put_item.call_args.args[0]
        assert written["kickoff_time"] == "2025-09-28T20:25Z"
        assert written["event_date"] == "2025-09-28"

    def test_dry_run_never_writes(self):
        mock_s3 = MagicMock()
        mock_s3.list_keys.return_value = ["nfl/scoreboard/2025/2/4.json"]
        mock_s3.get_json.return_value = {"events": [_scoreboard_event()]}
        mock_events_table = MagicMock()

        migration.migrate(mock_s3, mock_events_table, dry_run=True)

        mock_events_table.put_item.assert_not_called()

    def test_handles_multiple_scoreboard_objects_and_events(self):
        mock_s3 = MagicMock()
        mock_s3.list_keys.return_value = ["nfl/scoreboard/2025/2/4.json", "nfl/scoreboard/2025/2/5.json"]
        mock_s3.get_json.side_effect = [
            {"events": [_scoreboard_event("1", "12", "24"), _scoreboard_event("2", "7", "13")]},
            {"events": [_scoreboard_event("3", "9", "8")]},
        ]
        mock_events_table = MagicMock()

        migration.migrate(mock_s3, mock_events_table, dry_run=False)

        assert mock_events_table.put_item.call_count == 3

    def test_empty_scoreboard_is_a_no_op(self):
        mock_s3 = MagicMock()
        mock_s3.list_keys.return_value = ["nfl/scoreboard/2025/2/4.json"]
        mock_s3.get_json.return_value = {"events": []}
        mock_events_table = MagicMock()

        migration.migrate(mock_s3, mock_events_table, dry_run=False)

        mock_events_table.put_item.assert_not_called()


class TestMain:
    def test_dry_run_flag_is_threaded_through(self):
        with patch.object(migration, "migrate") as mock_migrate, \
             patch.object(migration, "S3Manager"), \
             patch.object(migration, "DynamoDBTable"), \
             patch("sys.argv", ["backfill_kickoff_time.py", "--dry-run"]), \
             patch.dict("os.environ", {"RAW_BUCKET_NAME": "b", "EVENTS_TABLE_NAME": "t"}):
            migration.main()

        assert mock_migrate.call_args.args[-1] is True
