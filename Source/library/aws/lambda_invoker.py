"""Thin wrapper around boto3's Lambda client for a fire-and-forget async invoke."""
import json

import boto3


class LambdaInvoker:
    def __init__(self, function_name: str, region: str | None = None):
        self.function_name = function_name
        self._client = boto3.client("lambda", region_name=region)

    def invoke_async(self, payload: dict) -> None:
        self._client.invoke(
            FunctionName=self.function_name,
            InvocationType="Event",
            Payload=json.dumps(payload).encode("utf-8"),
        )
