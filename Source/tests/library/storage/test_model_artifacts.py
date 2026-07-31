"""
Unit tests for library.storage.model_artifacts -- the versioned S3 key
scheme shared by every sport's training task (win-probability,
score-margin, per-stat player props all use these same helpers).
"""
from library.storage.model_artifacts import model_artifact_key, model_artifact_prefix, next_model_version


class TestModelArtifactPrefix:
    def test_builds_sport_and_model_scoped_prefix(self):
        assert model_artifact_prefix("nfl", "win-probability") == "nfl/win-probability/"


class TestNextModelVersion:
    def test_returns_one_when_no_versions_exist(self):
        assert next_model_version([]) == 1

    def test_returns_max_plus_one(self):
        keys = [
            "nfl/win-probability/v1/model.xgb",
            "nfl/win-probability/v1/metadata.json",
            "nfl/win-probability/v2/model.xgb",
        ]

        assert next_model_version(keys) == 3

    def test_ignores_keys_without_a_version_segment(self):
        keys = ["nfl/win-probability/model.xgb"]

        assert next_model_version(keys) == 1


class TestModelArtifactKey:
    def test_builds_full_versioned_key(self):
        key = model_artifact_key("nfl", "win-probability", 3, "model.xgb")

        assert key == "nfl/win-probability/v3/model.xgb"
