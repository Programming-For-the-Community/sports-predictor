"""The real predict-read configs: head-to-head sports list a date's games;
golf and racing don't."""
import json
from unittest.mock import patch

import pytest

import shared_predict_read


def _get(sport, query):
    return shared_predict_read.lambda_handler({"resource": f"/{sport}/events", "queryStringParameters": query}, None)


@pytest.mark.parametrize("sport", ["nfl", "nba", "ncaafb", "ncaambb", "nhl"])
def test_head_to_head_sports_pass_the_date_through(sport):
    reads = getattr(shared_predict_read, f"{sport}_reads")
    with patch.object(shared_predict_read, "_get_storage"), patch.object(shared_predict_read, "_get_predictions_table"), \
         patch.object(shared_predict_read, "_get_model_bucket"), \
         patch.object(reads, "list_events", return_value={"events": []}) as list_events:
        response = _get(sport, {"status": "completed", "date": "2026-11-01"})

    assert response["statusCode"] == 200
    assert list_events.call_args.kwargs["event_date"] == "2026-11-01"
    assert list_events.call_args.args[2:4] == (sport, "completed")


@pytest.mark.parametrize("sport", ["pga", "f1"])
def test_field_sports_refuse_a_date(sport):
    response = _get(sport, {"status": "completed", "date": "2026-11-01"})
    assert response["statusCode"] == 400
    assert "by date" in json.loads(response["body"])["error"]
