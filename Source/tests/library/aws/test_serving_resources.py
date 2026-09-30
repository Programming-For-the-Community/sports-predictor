"""
Unit tests for library.aws.serving_resources -- the lazily created
FeatureStorage/model-bucket/predictions-table singletons every predict
and serving Lambda shares, cached in the handler module's own globals.
"""
from unittest.mock import patch

from library.aws import serving_resources


def _env(monkeypatch):
    monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "models")
    monkeypatch.setenv("PREDICTIONS_TABLE_NAME", "predictions")
    monkeypatch.setenv("AWS_REGION", "us-east-1")


def test_each_resource_is_built_once_from_the_environment(monkeypatch):
    _env(monkeypatch)
    namespace = {}
    resources = serving_resources.ServingResources(namespace)

    with patch.object(serving_resources, "FeatureStorage") as storage_cls, \
            patch.object(serving_resources, "S3Manager") as s3_cls, \
            patch.object(serving_resources, "DynamoDBTable") as table_cls:
        for _ in range(2):
            assert resources.all() == (storage_cls.return_value, s3_cls.return_value, table_cls.return_value)

    storage_cls.assert_called_once_with()
    s3_cls.assert_called_once_with("models", region="us-east-1")
    table_cls.assert_called_once_with("predictions", region="us-east-1")
    assert namespace == {
        "_storage": storage_cls.return_value,
        "_model_bucket": s3_cls.return_value,
        "_predictions_table": table_cls.return_value,
    }


def test_a_value_already_in_the_namespace_is_used_as_is(monkeypatch):
    _env(monkeypatch)
    namespace = {"_storage": "preset-storage"}

    with patch.object(serving_resources, "FeatureStorage") as storage_cls:
        assert serving_resources.ServingResources(namespace).storage() == "preset-storage"

    storage_cls.assert_not_called()
