"""
Unit tests for migrations/backfill_sport_attribute.py. DynamoDBTable is
mocked throughout -- these verify the migration's own logic (which rows
get patched, what they're patched with, and that it never writes when
there's nothing to do or --dry-run is set), not real DynamoDB behavior.
"""
import os
from unittest.mock import MagicMock, patch

import backfill_sport_attribute as migration

os.environ.setdefault("PLAYER_GAME_STATS_TABLE_NAME", "test-player-game-stats")
os.environ.setdefault("TEAM_GAME_STATS_TABLE_NAME", "test-team-game-stats")


class TestMigrateTable:
    def test_patches_rows_missing_sport_deriving_it_from_event_key(self):
        mock_table = MagicMock()
        mock_table.scan.return_value = [
            {"event_key": "SPORT#NFL#EVENT#1", "player_key": "SPORT#NFL#PLAYER#qb1"},
        ]

        with patch.object(migration, "DynamoDBTable", return_value=mock_table):
            migration.migrate_table("PLAYER_GAME_STATS_TABLE_NAME", ["event_key", "player_key"], None, dry_run=False)

        written = mock_table.batch_write.call_args.args[0]
        assert written == [{"event_key": "SPORT#NFL#EVENT#1", "player_key": "SPORT#NFL#PLAYER#qb1", "sport": "nfl"}]
        assert mock_table.batch_write.call_args.kwargs["key_names"] == ["event_key", "player_key"]

    def test_skips_rows_that_already_have_sport(self):
        mock_table = MagicMock()
        mock_table.scan.return_value = [
            {"event_key": "SPORT#NFL#EVENT#1", "player_key": "SPORT#NFL#PLAYER#qb1", "sport": "nfl"},
            {"event_key": "SPORT#NFL#EVENT#2", "player_key": "SPORT#NFL#PLAYER#qb2"},
        ]

        with patch.object(migration, "DynamoDBTable", return_value=mock_table):
            migration.migrate_table("PLAYER_GAME_STATS_TABLE_NAME", ["event_key", "player_key"], None, dry_run=False)

        written = mock_table.batch_write.call_args.args[0]
        assert len(written) == 1
        assert written[0]["event_key"] == "SPORT#NFL#EVENT#2"

    def test_does_not_write_when_every_row_already_has_sport(self):
        mock_table = MagicMock()
        mock_table.scan.return_value = [
            {"event_key": "SPORT#NFL#EVENT#1", "player_key": "SPORT#NFL#PLAYER#qb1", "sport": "nfl"},
        ]

        with patch.object(migration, "DynamoDBTable", return_value=mock_table):
            migration.migrate_table("PLAYER_GAME_STATS_TABLE_NAME", ["event_key", "player_key"], None, dry_run=False)

        mock_table.batch_write.assert_not_called()

    def test_dry_run_never_writes(self):
        mock_table = MagicMock()
        mock_table.scan.return_value = [
            {"event_key": "SPORT#NFL#EVENT#1", "player_key": "SPORT#NFL#PLAYER#qb1"},
        ]

        with patch.object(migration, "DynamoDBTable", return_value=mock_table):
            migration.migrate_table("PLAYER_GAME_STATS_TABLE_NAME", ["event_key", "player_key"], None, dry_run=True)

        mock_table.batch_write.assert_not_called()

    def test_derives_sport_correctly_for_team_game_stats_shape(self):
        mock_table = MagicMock()
        mock_table.scan.return_value = [{"event_key": "SPORT#NFL#EVENT#1", "team_key": "TEAM#12"}]

        with patch.object(migration, "DynamoDBTable", return_value=mock_table):
            migration.migrate_table("TEAM_GAME_STATS_TABLE_NAME", ["event_key", "team_key"], None, dry_run=False)

        written = mock_table.batch_write.call_args.args[0]
        assert written[0]["sport"] == "nfl"


class TestMain:
    def test_default_migrates_both_tables(self):
        with patch.object(migration, "migrate_table") as mock_migrate, \
             patch("sys.argv", ["backfill_sport_attribute.py"]):
            migration.main()

        assert mock_migrate.call_count == 2
        migrated_envs = {call.args[0] for call in mock_migrate.call_args_list}
        assert migrated_envs == {"PLAYER_GAME_STATS_TABLE_NAME", "TEAM_GAME_STATS_TABLE_NAME"}

    def test_table_flag_scopes_to_just_that_table(self):
        with patch.object(migration, "migrate_table") as mock_migrate, \
             patch("sys.argv", ["backfill_sport_attribute.py", "--table", "team_game_stats"]):
            migration.main()

        mock_migrate.assert_called_once()
        assert mock_migrate.call_args.args[0] == "TEAM_GAME_STATS_TABLE_NAME"

    def test_dry_run_flag_is_threaded_through(self):
        with patch.object(migration, "migrate_table") as mock_migrate, \
             patch("sys.argv", ["backfill_sport_attribute.py", "--dry-run"]):
            migration.main()

        for call in mock_migrate.call_args_list:
            assert call.args[3] is True
