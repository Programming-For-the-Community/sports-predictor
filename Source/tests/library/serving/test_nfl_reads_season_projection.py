"""
Unit tests for library.serving.nfl_reads.get_season_projection -- GET
/nfl/season's read of the cached S3 object the weekly scheduled Fargate
job writes, None (not an error) when that job hasn't run yet. S3 is
mocked. Split out of what used to be one large test_nfl_reads.py -- see
test_nfl_reads_list_events.py's own history note.
"""
from unittest.mock import MagicMock

from library.serving import nfl_reads


class TestGetSeasonProjection:
    def test_returns_the_cached_projection_from_its_own_key(self):
        s3 = MagicMock()
        s3.object_exists.return_value = True
        s3.get_json.return_value = {"sport": "nfl", "season": 2025, "standings": []}

        result = nfl_reads.get_season_projection(s3, "nfl")

        assert result == {"sport": "nfl", "season": 2025, "standings": []}
        s3.get_json.assert_called_once_with("season-projections/nfl/latest.json")

    def test_returns_none_when_the_scheduled_job_hasnt_written_one_yet(self):
        s3 = MagicMock()
        s3.object_exists.return_value = False

        result = nfl_reads.get_season_projection(s3, "nfl")

        assert result is None
