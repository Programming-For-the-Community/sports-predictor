"""GET /{sport}/events?date=YYYY-MM-DD: the route, and the query each
list_events makes for it (that date only, no snapshot)."""
import json
from unittest.mock import MagicMock, patch

from library.serving import common
from library.serving.predict_read_handler import make_multi_sport_lambda_handler


def _handler(**config):
    configs = {"nba": {"list_events_fn": MagicMock(return_value={"events": []}), "get_season_projection_fn": MagicMock(),
                       "list_models_fn": MagicMock(), "freshness_inputs_fn": MagicMock(), "has_player_prop_route": False, **config}}
    namespace = {"_get_storage": MagicMock(return_value="storage")}
    handler = make_multi_sport_lambda_handler(sport_configs=configs, namespace=namespace, logger=MagicMock(),
                                              response_fn=lambda code, body: {"statusCode": code, "body": json.dumps(body)})
    return handler, configs["nba"]["list_events_fn"]


def _get(handler, query):
    return handler({"resource": "/nba/events", "queryStringParameters": query}, None)


def test_a_sport_that_lists_by_date_gets_the_date():
    handler, list_events = _handler(lists_by_date=True)
    assert _get(handler, {"status": "completed", "date": "2026-11-01"})["statusCode"] == 200
    list_events.assert_called_once_with("storage", "completed", event_date="2026-11-01")


def test_without_a_date_the_call_is_unchanged():
    handler, list_events = _handler(lists_by_date=True)
    _get(handler, {"status": "scheduled"})
    list_events.assert_called_once_with("storage", "scheduled")


def test_a_bad_date_or_a_sport_without_dates_is_a_400():
    handler, list_events = _handler(lists_by_date=True)
    assert _get(handler, {"status": "completed", "date": "11/01/2026"})["statusCode"] == 400
    assert _get(handler, {"status": "completed", "date": "2026-02-30"})["statusCode"] == 400
    field_handler, field_list = _handler()
    response = _get(field_handler, {"status": "completed", "date": "2026-11-01"})
    assert response["statusCode"] == 400
    assert "by date" in json.loads(response["body"])["error"]
    list_events.assert_not_called()
    field_list.assert_not_called()


def test_select_list_events_queries_only_that_date_and_skips_grouping():
    storage = MagicMock()
    storage.get_all_events.return_value = [{"event_id": "1", "pro_bowl": True}, {"event_id": "2"}]
    latest = MagicMock()

    events = common.select_list_events(storage, "nfl", "completed", "2026-11-01", latest, MagicMock(), keep=lambda e: not e.get("pro_bowl"))

    storage.get_all_events.assert_called_once_with("nfl", status="completed", since_date="2026-11-01", until_date="2026-11-01")
    assert events == [{"event_id": "2"}]
    latest.assert_not_called()


def test_a_dated_list_never_touches_the_latest_day_snapshot():
    storage = MagicMock()
    storage.get_all_events.return_value = [{"event_id": "1", "event_key": "k1", "event_date": "2026-11-01", "status": "completed"}]
    with patch.object(common, "read_completed_snapshot", return_value=(None, None)) as read_snapshot, \
         patch.object(common, "write_completed_snapshot") as write_snapshot, \
         patch.object(common, "prefetch_participant_teams", return_value={}), \
         patch.object(common, "event_prediction_rows", return_value=[]):
        result = common.list_events_grouped_by_day(storage, MagicMock(), "nba", "completed", model_bucket="bucket", event_date="2026-11-01")

    assert [e["event_id"] for e in result["events"]] == ["1"]
    assert read_snapshot.call_args.args[0] is None
    write_snapshot.assert_called_once()
    assert write_snapshot.call_args.args[2] is None  # no fingerprint, so nothing is written
