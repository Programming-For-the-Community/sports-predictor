"""
Thin wrapper around boto3's S3 client for the patterns ingest/backfill
jobs repeat across sports: check-before-fetch, put-as-json, get-as-json.
Not a general-purpose S3 SDK replacement -- only the operations actually
used by sport adapters live here; extend as new needs show up.
"""
import json

import boto3
from botocore.exceptions import ClientError


class S3Manager:
    def __init__(self, bucket: str, region: str | None = None):
        self.bucket = bucket
        self._client = boto3.client("s3", region_name=region)

    def object_exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
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
        )

    def get_json(self, key: str) -> dict:
        response = self._client.get_object(Bucket=self.bucket, Key=key)
        return json.loads(response["Body"].read())

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def get_bytes(self, key: str) -> bytes:
        response = self._client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def list_keys(self, prefix: str) -> list[str]:
        keys = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return keys
