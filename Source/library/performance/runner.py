"""
Builds and stores one sport's model-performance scorecard: reads the current
season's completed events and each one's frozen pre-event prediction rows, hands
them to the sport's extraction (library.performance.head_to_head for the team
sports -- which also needs player stat lines for player props --
library.performance.field_events for PGA/F1), and writes the result to S3 for
GET /{sport}/model-performance. The shared model-performance Lambda
(aws-lambdas/shared/model-performance) calls run_sport once per sport.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from boto3.dynamodb.conditions import Key

from library.performance import field_events
from library.performance.head_to_head import build_head_to_head_scorecard
from library.serving.common import list_models
from library.serving.prediction_snapshots import event_prediction_rows
from library.storage.model_performance import model_performance_key

# Wide enough to reach the start of any sport's current season (they all begin
# within the previous 12 months); the season filter below does the real scoping.
_LOOKBACK_DAYS = 400
# A sport with too many events to grade all season in one daily job is graded on
# a rolling window instead: NCAAMBB plays thousands of games. Its scorecard then
# covers just this window, and says so (`window_days`).
WINDOW_DAYS = {"ncaambb": 7}
_MAX_WORKERS = 16
_PROP_ROW_MARKER = "MODEL#player-prop-"


def _current_season_events(storage, sport: str, today: date) -> tuple[int | None, list[dict]]:
    days = WINDOW_DAYS.get(sport, _LOOKBACK_DAYS)
    events = storage.get_all_events(sport, "completed", since_date=(today - timedelta(days=days)).isoformat())
    seasons = [e["season"] for e in events if e.get("season") is not None]
    season = max(seasons) if seasons else None
    return season, [e for e in events if e.get("season") == season]


def _next_event(storage, sport: str, today: date) -> dict | None:
    """The soonest event from today on that isn't final yet (a live game is
    still "scheduled") -- None in the off-season."""
    upcoming = storage.get_all_events(sport, "scheduled", scan_index_forward=True, limit=1, since_date=today.isoformat())
    return upcoming[0] if upcoming else None


def run_sport(sport: str, storage, predictions_table, s3, today: date) -> dict:
    if sport in field_events.SPORTS:
        return _run_field_sport(sport, storage, predictions_table, s3, today)
    return _run_head_to_head_sport(sport, storage, predictions_table, s3, today)


def _run_field_sport(sport: str, storage, predictions_table, s3, today: date) -> dict:
    """PGA/F1: the raw rows go to the extraction as-is -- a round model is
    graded against its own round's snapshot, not one event-wide label."""
    season, events = _current_season_events(storage, sport, today)

    def _raw_rows(event: dict) -> list[dict]:
        return predictions_table.query(Key("event_key").eq(event["event_key"]))

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
        raw_rows_by_event = {e["event_key"]: rows for e, rows in zip(events, executor.map(_raw_rows, events))}

    document = field_events.build_field_scorecard(sport, season, events, raw_rows_by_event, list_models(s3, sport)["models"])
    document["coverage"] = _coverage(events, raw_rows_by_event)
    _name_best(storage, sport, document)
    _stamp_window(document, sport)
    s3.put_json(model_performance_key(sport), document)
    return document


def _run_head_to_head_sport(sport: str, storage, predictions_table, s3, today: date) -> dict:
    season, events = _current_season_events(storage, sport, today)

    def _rows(event: dict) -> list[dict]:
        return event_prediction_rows(predictions_table, event["event_key"])

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
        rows_by_event = {e["event_key"]: rows for e, rows in zip(events, executor.map(_rows, events))}

        def _stats(event_key: str) -> dict[str, dict]:
            return {row["entity_id"]: row.get("stat_line", {}) for row in storage.get_player_game_stats_for_event(event_key)}

        # Only events with player-prop predictions need their stat lines.
        with_props = [key for key, rows in rows_by_event.items() if any(r["model_key"].startswith(_PROP_ROW_MARKER) for r in rows)]
        stats_by_event = dict(zip(with_props, executor.map(_stats, with_props)))

    document = build_head_to_head_scorecard(
        sport, season, events, rows_by_event, stats_by_event, list_models(s3, sport)["models"], _next_event(storage, sport, today),
    )
    document["coverage"] = _coverage(events, rows_by_event)
    _name_best(storage, sport, document)
    _stamp_window(document, sport)
    s3.put_json(model_performance_key(sport), document)
    return document


def _name_best(storage, sport: str, document: dict) -> None:
    """Adds each ranked team's or player's name, abbreviation and color, in
    one batched entity read across every record."""
    lists = [record["best"] for record in document["models"] if "best" in record]
    refs = [(entry["entity_id"], ranked["entity_type"]) for ranked in lists for entry in ranked["entities"]]
    entities = storage.get_entities(sport, refs) if refs else {}
    for ranked in lists:
        for entry in ranked["entities"]:
            entity = entities.get((entry["entity_id"], ranked["entity_type"])) or {}
            metadata = entity.get("metadata") or {}
            entry.update(name=entity.get("name"), abbreviation=metadata.get("abbreviation"), color=metadata.get("color"))


def _stamp_window(document: dict, sport: str) -> None:
    """Says so on the scorecard when it covers a rolling window, not the season."""
    if sport in WINDOW_DAYS:
        document["window_days"] = WINDOW_DAYS[sport]


def _coverage(events: list[dict], rows_by_event: dict[str, list[dict]]) -> dict:
    """How many of the season's completed events had a prediction to grade --
    the audit that catches an event nobody predicted before it was played."""
    with_prediction = sum(1 for e in events if rows_by_event.get(e["event_key"]))
    return {"completed_events": len(events), "with_prediction": with_prediction, "unpredicted": len(events) - with_prediction}
