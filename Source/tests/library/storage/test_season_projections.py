"""
Unit tests for library.storage.season_projections -- the S3 key the
scheduled compute path writes to and get_season_projection reads from.
"""
from library.storage.season_projections import season_projection_key


class TestSeasonProjectionKey:
    def test_builds_a_sport_scoped_key_outside_the_model_artifact_namespace(self):
        key = season_projection_key("nfl")

        assert key == "season-projections/nfl/latest.json"
        # Must NOT start with "nfl/" -- nfl_reads.list_models scans
        # s3.list_keys("nfl/") and treats every top-level segment under it
        # as a model name; this key deliberately lives outside that prefix.
        assert not key.startswith("nfl/")
