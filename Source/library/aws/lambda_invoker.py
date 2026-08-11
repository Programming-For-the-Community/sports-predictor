"""
Thin wrapper around boto3's Lambda client for firing an async ("fire and
forget") invoke -- the one operation library.storage.prediction_cache's
populate-on-miss design needs (predict-read triggering the heavy predict
Lambda to compute and cache a result in the background). Not a general-
purpose Lambda SDK replacement, same narrow-scope philosophy as
S3Manager/DynamoDBTable.
"""
import json

import boto3


class LambdaInvoker:
    def __init__(self, function_name: str, region: str | None = None):
        self.function_name = function_name
        self._client = boto3.client("lambda", region_name=region)

    def invoke_async(self, payload: dict) -> None:
        """Fires payload at this Lambda with InvocationType='Event' --
        the caller gets an immediate ack from Lambda's own Invoke API
        without waiting for the invoked function to even start, let
        alone finish. A failure once it actually runs shows up in
        CloudWatch/as an async invocation failure (Lambda retries an
        Event invoke twice by default on error) -- never raised back
        here."""
        self._client.invoke(
            FunctionName=self.function_name,
            InvocationType="Event",
            Payload=json.dumps(payload).encode("utf-8"),
        )
