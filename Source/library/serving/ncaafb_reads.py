"""
Read-only NCAAFB serving logic -- GET /ncaafb/events, GET /ncaafb/models,
and GET /ncaafb/season -- shared between the heavy inference Lambda
(Source/aws-lambdas/ncaafb/predict) and the light read-only Lambda
(Source/aws-lambdas/ncaafb/predict-read).

`_home_and_away`/`_actual_result`/`_prediction_comparison`/
`_leaders_comparison`/`list_models`/`get_season_projection`/
`WIN_PROBABILITY_MODEL`/`SCORE_MODELS` are thin re-exports of
library.serving.common (confirmed identical across nfl/nba/ncaafb/ncaambb
before sharing there -- see that module's own docstrings). What stays
genuinely NCAAFB-owned here: week-grouping (`_week_key`/
`_previous_week_events`/`_next_week_events`, a genuinely different date-
gap-clustering algorithm from NFL's own fixed-cutoff one -- CFBD's flat
postseason week numbering needs it), the CFP/Bowl round-name rule
(`_round_label`), and list_events itself, which wires those together in a
shape NFL's own list_events (different week-clustering algorithm, a
Pro-Bowl/exhibition filter NCAAFB has no equivalent of) doesn't share
closely enough to also fold in here.

Callers own their own storage/s3/predictions_table objects and Lambda-
lifecycle concerns.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from boto3.dynamodb.conditions import Key

from library.features.ncaafb import is_bowl_game, is_playoff_game
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


def _week_key(event: dict) -> tuple:
    """Groups events into what the frontend shows as one "week" --
    postseason games key by their own event_date, not `week`: CFBD's
    postseason week numbering is flat (every bowl/CFP round across an
    entire season comes back as week=1), so keying postseason by `week`
    the same way regular season does would group the entire bracket as
    one "week". event_date isolates each round correctly instead, since a
    postseason round's own games all share a date and rounds virtually
    never overlap. Regular season keeps `week`, unaffected."""
    if event.get("season_type") == "postseason":
        return (event.get("season"), event.get("season_type"), event.get("event_date"))
    return (event.get("season"), event.get("season_type"), event.get("week"))


def _previous_week_events(completed: list[dict]) -> list[dict]:
    """Only the most recently completed week's games."""
    if not completed:
        return []
    latest = max(completed, key=lambda e: e.get("event_date", ""))
    target = _week_key(latest)
    return [e for e in completed if _week_key(e) == target]


_STALE_SCHEDULED_GRACE_DAYS = 3  # tolerates ingest lag flipping a played game's status to completed

# The largest calendar gap between two consecutive game-dates that still
# counts as the same real week -- every real week's own internal
# date-to-date gaps are at most 2 days (e.g. week 8's Oct 20 -> Oct 22),
# while week 1's tagged games can split into two clusters (e.g. Aug
# 29-30, then Sep 3-7) with a larger gap between them -- see
# _next_week_events' own docstring for why a single fixed-span-from-the-
# earliest-date cap isn't tight enough to separate those two clusters.
_MAX_INTRA_WEEK_GAP_DAYS = 2


def _next_week_events(scheduled: list[dict]) -> list[dict]:
    """Only the soonest upcoming week's games. Events older than
    _STALE_SCHEDULED_GRACE_DAYS are excluded before finding the soonest,
    so a stale "scheduled" game doesn't win min() permanently. The grace
    period only decides which week counts as "soonest" (so an
    already-played Thursday game doesn't hide the rest of its own week's
    Saturday games) -- the returned list is separately filtered to
    today-or-later, since "upcoming" must never include a past-dated
    event, played or not.

    Also clusters results by date gap, not just the `week` tag -- confirmed
    live, 2026-08-24: week 1's own games actually split into two clusters,
    Aug 29-30 (a handful of true season-opener games, "Week 0" by fan
    convention) and Sep 3-7 (the following weekend's much larger main
    slate), both sharing CFBD's same week=1 tag rather than CFBD assigning
    the openers their own separate week number. A first attempt capped
    results to a fixed number of days from the soonest game's date, but
    that's anchored to the wrong end -- Sep 3-4 are only 3-4 days after
    Aug 29, comfortably inside any span cap wide enough to hold Aug 29-30
    together, so it still merged the two clusters. Walking the sorted
    distinct dates and cutting off at the first gap wider than
    _MAX_INTRA_WEEK_GAP_DAYS (measured between *consecutive* dates, not
    from the start) correctly isolates just the soonest cluster -- Aug 30
    -> Sep 3 is a 3-day gap, past the 2-day threshold -- without merging
    a normal week's own internal 1-2 day gaps between game days.

    Tries each candidate week in earliest-first order, not just the very
    first one -- a week whose games have ALL already been played (but
    ingest hasn't flipped their status to "completed" yet, so they're
    still "scheduled" and within the grace period) has every one of its
    own events fall below the today-or-later filter, leaving nothing to
    return for that week; the real next week, sitting right alongside it
    in the same candidate pool, must still be found instead of this
    function giving up."""
    # event_date is a calendar day in ESPN/CFBD's own U.S.-Eastern
    # bucketing (see library/parsing.py's us_eastern_date), not a
    # UTC date -- comparing it against a raw UTC "today" drops the whole
    # day's games from this list the moment the server clock crosses UTC
    # midnight, which for a 6pm+ Eastern kickoff is while it's still being
    # played. Deriving "today" the same Eastern way keeps both sides on
    # the same calendar.
    today = us_eastern_date(datetime.now(timezone.utc))
    cutoff = us_eastern_date(datetime.now(timezone.utc) - timedelta(days=_STALE_SCHEDULED_GRACE_DAYS))
    plausible = [e for e in scheduled if e.get("event_date", "") >= cutoff]
    if not plausible:
        return []

    weeks_by_earliest_date: dict[tuple, str] = {}
    for e in plausible:
        key = _week_key(e)
        event_date = e.get("event_date", "")
        if key not in weeks_by_earliest_date or event_date < weeks_by_earliest_date[key]:
            weeks_by_earliest_date[key] = event_date

    same_week: list[dict] = []
    for target in sorted(weeks_by_earliest_date, key=lambda k: weeks_by_earliest_date[k]):
        same_week = [e for e in plausible if _week_key(e) == target and e.get("event_date", "") >= today]
        if same_week:
            break
    if not same_week:
        return []
    distinct_dates = sorted({e.get("event_date", "") for e in same_week})
    cluster_dates = {distinct_dates[0]}
    for previous, current in zip(distinct_dates, distinct_dates[1:]):
        gap = (date.fromisoformat(current) - date.fromisoformat(previous)).days
        if gap > _MAX_INTRA_WEEK_GAP_DAYS:
            break
        cluster_dates.add(current)
    return [e for e in same_week if e.get("event_date", "") in cluster_dates]


def _round_label(event: dict) -> str | None:
    """None for a regular-season game; "CFP" for a real 12-team playoff
    game (is_playoff_game); "Bowl" for any other postseason game."""
    if event.get("season_type") != "postseason":
        return None
    return "CFP" if is_playoff_game(event) else "Bowl" if is_bowl_game(event) else None


def list_events(storage, predictions_table, sport: str, status: str) -> dict:
    """GET /ncaafb/events?status=scheduled|completed -- scoped to exactly
    one week, not the whole matching history. No exhibition-game filter --
    NCAAFB has no equivalent contamination to exclude. Each participant
    also carries `name`/`abbreviation` off its own team entity -- see
    enrich_participants. Also carries `venue_name`/`venue_city`/
    `venue_state` straight off the stored event, `null` on any of the
    three the venue lacked.

    Bounded to RECENT_EVENTS_LIMIT rows on the query itself, most-recent-
    or soonest-first to match whichever bucket status narrows down to
    below -- an unbounded get_all_events call here would paginate through
    the sport's entire completed/scheduled history before ever
    discarding everything but one week (see pga_reads.py's own
    list_events docstring)."""
    if status == "completed":
        events = storage.get_all_events(sport, status=status, limit=RECENT_EVENTS_LIMIT)
        events = _previous_week_events(events)
    elif status == "scheduled":
        events = storage.get_all_events(sport, status=status, scan_index_forward=True, limit=RECENT_EVENTS_LIMIT)
        events = _next_week_events(events)
    else:
        events = storage.get_all_events(sport, status=status)

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
            rows = predictions_table.query(Key("event_key").eq(e["event_key"]))
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
