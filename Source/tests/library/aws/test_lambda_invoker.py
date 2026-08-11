"""
Unit tests for LambdaInvoker.invoke_async -- the fire-and-forget async
invoke library.storage.prediction_cache's populate-on-miss design relies
on (predict-read triggering the heavy predict Lambda in the background).
"""
import json
from unittest.mock import patch

from library.aws.lambda_invoker import LambdaInvoker


def _make_invoker():
    with patch("library.aws.lambda_invoker.boto3") as mock_boto3:
        mock_client = mock_boto3.client.return_value
        invoker = LambdaInvoker("sports-predictor-nfl-predict", region="us-east-1")
    return invoker, mock_client


class TestInvokeAsync:
    def test_invokes_with_event_invocation_type(self):
        invoker, mock_client = _make_invoker()

        invoker.invoke_async({"detail-type": "ComputeAndCachePrediction"})

        kwargs = mock_client.invoke.call_args.kwargs
        assert kwargs["FunctionName"] == "sports-predictor-nfl-predict"
        assert kwargs["InvocationType"] == "Event"

    def test_payload_is_json_encoded(self):
        invoker, mock_client = _make_invoker()

        invoker.invoke_async({"route": "event", "event_id": "1"})

        payload = json.loads(mock_client.invoke.call_args.kwargs["Payload"])
        assert payload == {"route": "event", "event_id": "1"}
