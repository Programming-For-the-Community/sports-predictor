"""
Real postseason lookups every head-to-head sport's season_projection.py
feeds into library.serving.bracket_simulation: the season's real playoff
games, and each real game's own logged pre-game win probability.
"""
import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from types import ModuleType

from boto3.dynamodb.conditions import Key

from library.parsing import us_eastern_date
from library.serving.common import WIN_PROBABILITY_MODEL, latest_matching_row


def real_postseason_events(storage, sport: str, current_season: int | None, is_postseason: Callable[[dict], bool]) -> list[dict]:
    """Every real postseason game this season, scheduled or completed.
    get_all_events has no "every status at once" option, so both statuses
    are fetched and merged."""
    return [
        event
        for status in ("scheduled", "completed")
        for event in storage.get_all_events(sport, status=status)
        if event.get("season") == current_season and is_postseason(event)
    ]


def logged_win_probability(predictions_table, event_key_value: str) -> dict | None:
    """This event's own logged win-probability prediction, if anyone ever
    requested one -- never recomputed for a finished game, since
    recomputing after the fact could build live features that already
    include this game's own result."""
    rows = predictions_table.query(Key("event_key").eq(event_key_value))
    row = latest_matching_row(rows, WIN_PROBABILITY_MODEL)
    return row["predicted_value"] if row else None


def logged_home_probability(event_prediction: ModuleType, storage, s3, predictions_table, logger: logging.Logger):
    """event -> the home side's logged pre-game win probability, computing
    and logging one for a real, unplayed game nobody has viewed yet; None
    when there is none or computing it fails."""
    def lookup(event: dict) -> float | None:
        logged = logged_win_probability(predictions_table, event["event_key"])
        if logged is None and event.get("status") != "completed":
            try:
                event_prediction.compute_and_cache_event(storage, s3, predictions_table, event["event_id"])
                logged = logged_win_probability(predictions_table, event["event_key"])
            except Exception:
                logger.exception("Failed computing a live prediction for bracket game %s", event["event_key"])
        return logged["home_win_probability"] if logged else None

    return lookup


# How long after the last real postseason game the daily rebuild keeps
# running, so the morning after a final still records it.
POSTSEASON_TRAILING_DAYS = 2


def postseason_active(storage, sport: str, is_postseason: Callable[[dict], bool], today: date | None = None) -> bool:
    """True while a real postseason game is scheduled, or one finished in
    the last POSTSEASON_TRAILING_DAYS days."""
    if any(is_postseason(event) for event in storage.get_all_events(sport, status="scheduled")):
        return True
    today = today or date.fromisoformat(us_eastern_date(datetime.now(timezone.utc)))
    since = (today - timedelta(days=POSTSEASON_TRAILING_DAYS)).isoformat()
    return any(is_postseason(event) for event in storage.get_all_events(sport, status="completed", since_date=since))
