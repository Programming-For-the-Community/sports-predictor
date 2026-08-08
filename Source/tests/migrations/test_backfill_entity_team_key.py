"""
Unit tests for migrations/backfill_entity_team_key.py. DynamoDBTable is
mocked throughout -- these verify the migration's own logic (which rows
get patched, what they're patched with, and that it never writes when
there's nothing to do or --dry-run is set), not real DynamoDB behavior.
"""
import os
from unittest.mock import MagicMock, patch

import backfill_entity_team_key as migration

os.environ.setdefault("ENTITIES_TABLE_NAME", "test-entities")


class TestMigrate:
    def test_patches_rows_missing_team_key_deriving_it_from_sport_and_team_id(self):
        mock_table = MagicMock()
        mock_table.scan.return_value = [
            {"entity_key": "SPORT#NFL#ENTITY#1", "sport": "nfl", "metadata": {"team_id": "26"}},
        ]

        with patch.object(migration, "DynamoDBTable", return_value=mock_table):
            migration.migrate(None, dry_run=False)

        written = mock_table.batch_write.call_args.args[0]
        assert written == [{
            "entity_key": "SPORT#NFL#ENTITY#1", "sport": "nfl", "metadata": {"team_id": "26"},
            "team_key": "SPORT#NFL#TEAM#26",
        }]
        assert mock_table.batch_write.call_args.kwargs["key_names"] == ["entity_key"]

    def test_skips_rows_that_already_have_team_key(self):
        mock_table = MagicMock()
        mock_table.scan.return_value = [
            {"entity_key": "SPORT#NFL#ENTITY#1", "sport": "nfl", "metadata": {"team_id": "26"},
             "team_key": "SPORT#NFL#TEAM#26"},
            {"entity_key": "SPORT#NFL#ENTITY#2", "sport": "nfl", "metadata": {"team_id": "12"}},
        ]

        with patch.object(migration, "DynamoDBTable", return_value=mock_table):
            migration.migrate(None, dry_run=False)

        written = mock_table.batch_write.call_args.args[0]
        assert len(written) == 1
        assert written[0]["entity_key"] == "SPORT#NFL#ENTITY#2"

    def test_skips_rows_with_no_team_id_at_all(self):
        # A team entity, or a player never associated with a team -- either
        # way there's no team_id to derive team_key from.
        mock_table = MagicMock()
        mock_table.scan.return_value = [
            {"entity_key": "SPORT#NFL#ENTITY#1", "sport": "nfl", "metadata": {}},
        ]

        with patch.object(migration, "DynamoDBTable", return_value=mock_table):
            migration.migrate(None, dry_run=False)

        mock_table.batch_write.assert_not_called()

    def test_does_not_write_when_every_row_already_has_team_key(self):
        mock_table = MagicMock()
        mock_table.scan.return_value = [
            {"entity_key": "SPORT#NFL#ENTITY#1", "sport": "nfl", "metadata": {"team_id": "26"},
             "team_key": "SPORT#NFL#TEAM#26"},
        ]

        with patch.object(migration, "DynamoDBTable", return_value=mock_table):
            migration.migrate(None, dry_run=False)

        mock_table.batch_write.assert_not_called()

    def test_dry_run_never_writes(self):
        mock_table = MagicMock()
        mock_table.scan.return_value = [
            {"entity_key": "SPORT#NFL#ENTITY#1", "sport": "nfl", "metadata": {"team_id": "26"}},
        ]

        with patch.object(migration, "DynamoDBTable", return_value=mock_table):
            migration.migrate(None, dry_run=True)

        mock_table.batch_write.assert_not_called()


class TestMain:
    def test_dry_run_flag_is_threaded_through(self):
        with patch.object(migration, "migrate") as mock_migrate, \
             patch("sys.argv", ["backfill_entity_team_key.py", "--dry-run"]):
            migration.main()

        assert mock_migrate.call_args.args[1] is True

    def test_default_is_not_a_dry_run(self):
        with patch.object(migration, "migrate") as mock_migrate, \
             patch("sys.argv", ["backfill_entity_team_key.py"]):
            migration.main()

        assert mock_migrate.call_args.args[1] is False
