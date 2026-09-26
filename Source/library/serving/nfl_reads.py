"""
Read-only NFL serving logic -- GET /nfl/events and GET /nfl/models --
shared between the heavy inference Lambda (Source/aws-lambdas/nfl/predict)
and the light read-only Lambda (Source/aws-lambdas/nfl/predict-read).
Neither route loads or deserializes an ML model artifact: list_models
only reads a model card's JSON metadata, list_events only reads events
and already-logged predictions from DynamoDB.

`_home_and_away`/`_actual_result`/`_prediction_comparison`/
`_leaders_comparison`/`list_models`/`get_season_projection`/
`WIN_PROBABILITY_MODEL`/`SCORE_MODELS` are thin re-exports of
library.serving.common (confirmed identical across nfl/nba/ncaafb/ncaambb
before sharing there -- see that module's own docstrings). What stays
genuinely NFL-owned here: week-grouping (`_week_key`/`_previous_week_events`/
`_next_week_events`), the postseason round-name mapping (`_round_label`/
POSTSEASON_ROUND_LABELS), the Pro Bowl/exhibition filter
(is_real_franchise_matchup), and list_events itself, which wires all of
those together in a shape NCAAFB's own list_events (different week-
clustering algorithm, different round-label rule, no exhibition filter)
doesn't share closely enough to also fold in here.

Callers own their own storage/s3/predictions_table objects and Lambda-
lifecycle concerns (lazy singletons, env var lookups); this module is
pure request-shaping logic.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from boto3.dynamodb.conditions import Key

from library.serving.prediction_snapshots import event_prediction_rows
from library.features.nfl_teams import is_real_franchise_matchup
from library.parsing import us_eastern_date
from library.serving import common
from library.serving.common import (
    RECENT_EVENTS_LIMIT,
    SCORE_MODELS,
    WIN_PROBABILITY_MODEL,
    enrich_participants,
    get_season_projection,
    list_models,
)

_home_and_away = common._home_and_away
_actual_result = common._actual_result
_prediction_comparison = common._prediction_comparison
_leaders_comparison = common._football_leaders_comparison

# season_type=3 (postseason) week -> round name (season 2020: week
# 1=Wild Card, 2=Divisional, 3=Conference Championship, 4=Pro Bowl,
# 5=Super Bowl). Week 4 is deliberately absent -- that's always the Pro
# Bowl, already excluded by is_real_franchise_matchup before this mapping
# is consulted, so a real week=4 postseason game should never occur.
POSTSEASON_ROUND_LABELS = {1: "Wild Card", 2: "Divisional", 3: "Conference Championship", 5: "Super Bowl"}


def _week_key(event: dict) -> tuple:
    return (event.get("season"), event.get("season_type"), event.get("week"))


def _previous_week_events(completed: list[dict]) -> list[dict]:
    """Only the most recently completed week's games -- not the full
    history. "Most recent" is the (season, season_type, week) of whichever
    completed event has the latest event_date; every other game sharing
    that same triple is part of the same week."""
    if not completed:
        return []
    latest = max(completed, key=lambda e: e.get("event_date", ""))
    target = _week_key(latest)
    return [e for e in completed if _week_key(e) == target]


_STALE_SCHEDULED_GRACE_DAYS = 3  # tolerates ingest lag flipping a played game's status to completed


def _next_week_events(scheduled: list[dict]) -> list[dict]:
    """Only the soonest upcoming week's games. Empty if nothing's been
    ingested yet for the next week (e.g. between seasons, before the
    Tuesday/Wednesday ingest schedule has run for it) -- the frontend
    shows a "coming soon" state for that case instead of an empty list
    that looks like a data problem.

    _STALE_SCHEDULED_GRACE_DAYS only gates which week counts as "next" --
    it ignores any "scheduled" event dated further back than that when
    picking the target week, so a game whose status was never updated
    can't win min() permanently and mask every real upcoming week behind
    it. It does NOT gate the returned events themselves: once the target
    week is picked, every event in that week is included even if its own
    date has aged past the grace window -- e.g. a mid-week game whose
    played-but-not-yet-flipped-to-completed status would otherwise vanish
    from this list days before the rest of its own week is done."""
    # event_date is a calendar day in ESPN's own U.S.-Eastern bucketing
    # (see library/parsing.py's us_eastern_date), not a UTC date --
    # deriving the cutoff the same Eastern way keeps it on the same
    # calendar as the event_date values it's compared against.
    cutoff = us_eastern_date(datetime.now(timezone.utc) - timedelta(days=_STALE_SCHEDULED_GRACE_DAYS))
    plausible = [e for e in scheduled if e.get("event_date", "") >= cutoff]
    if not plausible:
        return []
    earliest = min(plausible, key=lambda e: e.get("event_date", ""))
    target = _week_key(earliest)
    return [e for e in scheduled if _week_key(e) == target]


def _round_label(event: dict) -> str | None:
    """None for regular season (season_type=2) or any postseason week not
    in POSTSEASON_ROUND_LABELS -- a raw week number means nothing for the
    postseason (nobody thinks of the Super Bowl as "week 5"), but is
    exactly what a regular-season game should keep showing."""
    if event.get("season_type") != 3:
        return None
    return POSTSEASON_ROUND_LABELS.get(event.get("week"))


def list_events(storage, predictions_table, sport: str, status: str) -> dict:
    """GET /nfl/events?status=scheduled|completed -- scoped to exactly one
    week, not the whole matching history: status=scheduled returns the
    soonest upcoming week (empty if that week hasn't been ingested yet);
    status=completed returns the most recently completed week, each event
    carrying a `prediction_comparison` block (predicted vs. actual
    win/margin/score, `null` if no prediction was ever logged for that
    event before it was played). Every event also carries `round` -- the
    playoff round name for a postseason game, `null` for regular season.
    A completed event also carries `leaders_comparison` -- player-prop
    predicted-vs-actual, `null` under the same condition as
    `prediction_comparison`. Also carries `venue_name`/`venue_city`/
    `venue_state` straight off the stored event, `null` on any of the
    three the venue lacked. Excludes the Pro Bowl and any other exhibition
    game entirely. Each participant also carries `name`/`abbreviation`
    off its own team entity.

    Bounded to RECENT_EVENTS_LIMIT rows on the query itself, most-recent-
    or soonest-first to match whichever bucket status narrows down to
    below -- an unbounded get_all_events call here would paginate through
    the sport's entire completed/scheduled history before ever
    discarding everything but one week (see pga_reads.py's own
    list_events docstring)."""
    if status == "completed":
        raw_events = storage.get_all_events(sport, status=status, limit=RECENT_EVENTS_LIMIT)
    elif status == "scheduled":
        raw_events = storage.get_all_events(sport, status=status, scan_index_forward=True, limit=RECENT_EVENTS_LIMIT)
    else:
        raw_events = storage.get_all_events(sport, status=status)
    events = [e for e in raw_events if is_real_franchise_matchup(e)]

    if status == "completed":
        events = _previous_week_events(events)
    elif status == "scheduled":
        events = _next_week_events(events)

    def _entry(e: dict) -> dict:
        entry = {
            "event_id": e["event_id"],
            "event_date": e.get("event_date"),
            "kickoff_time": e.get("kickoff_time"),
            "status": e.get("status"),
            "season": e.get("season"),
            "season_type": e.get("season_type"),
            "week": e.get("week"),
            "round": _round_label(e),
            "participants": enrich_participants(storage, sport, e.get("participants")),
            "venue_name": e.get("venue_name"),
            "venue_city": e.get("venue_city"),
            "venue_state": e.get("venue_state"),
        }
        if status == "completed":
            # One query shared by _prediction_comparison and
            # _leaders_comparison rather than each querying independently.
            rows = event_prediction_rows(predictions_table, e["event_key"])
            entry["prediction_comparison"] = _prediction_comparison(rows, e)
            entry["leaders_comparison"] = _leaders_comparison(storage, rows, sport, e)
        return entry

    if not events:
        return {"sport": sport, "events": []}

    # Concurrent, not sequential -- each entry makes several DynamoDB round
    # trips (the predictions query above plus a get_entity per matched
    # leader candidate), independent per event.
    with ThreadPoolExecutor(max_workers=min(len(events), 16)) as executor:
        entries = list(executor.map(_entry, events))

    return {"sport": sport, "events": entries}
