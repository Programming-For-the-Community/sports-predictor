"""
The three per-container singletons every predict/serving Lambda reads
through -- FeatureStorage, the model-artifacts bucket, and the
predictions table -- created lazily on first use and cached in the
handler module's own globals (`_storage`, `_model_bucket`,
`_predictions_table`) via lambda_singletons.get_or_create, so a test that
sets or resets one of those module attributes is seen here too.
"""
import os

from library.aws import lambda_singletons
from library.aws.dynamodb_table import DynamoDBTable
from library.aws.s3_manager import S3Manager
from library.storage.feature_storage import FeatureStorage


class ServingResources:
    def __init__(self, namespace: dict) -> None:
        """namespace is the handler module's own globals()."""
        self._namespace = namespace

    def storage(self) -> FeatureStorage:
        return lambda_singletons.get_or_create(self._namespace, "_storage", FeatureStorage)

    def model_bucket(self) -> S3Manager:
        return lambda_singletons.get_or_create(
            self._namespace, "_model_bucket",
            lambda: S3Manager(os.environ["MODEL_ARTIFACTS_BUCKET_NAME"], region=os.environ.get("AWS_REGION")),
        )

    def predictions_table(self) -> DynamoDBTable:
        return lambda_singletons.get_or_create(
            self._namespace, "_predictions_table",
            lambda: DynamoDBTable(os.environ["PREDICTIONS_TABLE_NAME"], region=os.environ.get("AWS_REGION")),
        )

    def all(self) -> tuple[FeatureStorage, S3Manager, DynamoDBTable]:
        """(storage, model_bucket, predictions_table) -- the leading
        arguments every event_prediction entry point takes."""
        return self.storage(), self.model_bucket(), self.predictions_table()
