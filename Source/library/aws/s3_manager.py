"""
Thin wrapper around boto3's S3 client for the patterns ingest/backfill
jobs repeat across sports: check-before-fetch, put-as-json, get-as-json.
Not a general-purpose S3 SDK replacement -- only the operations actually
used by sport adapters live here; extend as new needs show up.
"""
import json

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from library.aws.account import get_account_id
from library.aws.boto_config import DEFAULT_CONFIG

# max_pool_connections raised above botocore's default (10) to support the
# serving Lambdas' 16-thread event-list fan-out.
_CONFIG = DEFAULT_CONFIG.merge(Config(max_pool_connections=25))


class S3Manager:
    def __init__(self, bucket: str, region: str | None = None, expected_bucket_owner: str | None = None):
        self.bucket = bucket
        self._client = boto3.client("s3", region_name=region, config=_CONFIG)
        self._owner = expected_bucket_owner or get_account_id()

    def object_exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=key, ExpectedBucketOwner=self._owner)
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def put_json(self, key: str, payload: dict) -> None:
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=json.dumps(payload).encode("utf-8"),
            ContentType="application/json",
            ExpectedBucketOwner=self._owner,
        )

    def get_json(self, key: str) -> dict:
        response = self._client.get_object(Bucket=self.bucket, Key=key, ExpectedBucketOwner=self._owner)
        return json.loads(response["Body"].read())

    def get_json_or_none(self, key: str) -> dict | None:
        """get_json, or None for a key that doesn't exist -- one GetObject
        round trip instead of object_exists + get_json's two. Needs
        s3:ListBucket on the bucket; without it S3 answers 403, not 404,
        for a missing key."""
        try:
            return self.get_json(key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self._client.put_object(
            Bucket=self.bucket, Key=key, Body=data, ContentType=content_type, ExpectedBucketOwner=self._owner,
        )

    def get_bytes(self, key: str) -> bytes:
        response = self._client.get_object(Bucket=self.bucket, Key=key, ExpectedBucketOwner=self._owner)
        return response["Body"].read()

    def delete_object(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key, ExpectedBucketOwner=self._owner)

    def list_keys(self, prefix: str) -> list[str]:
        keys = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix, ExpectedBucketOwner=self._owner):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return keys
