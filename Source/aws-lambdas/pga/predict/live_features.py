"""
Live (serving-time) PGA feature building -- re-derives each golfer's own
rolling history from current DynamoDB state and feeds it through the same
pure library/features/pga.py functions training uses, mirroring
aws-lambdas/nba/predict/live_features.py's "reuse the training-time
builder, re-derive its inputs live" pattern.

One storage.get_all_events(sport) call up front gives every golfer's
history in memory, so scoring a field is in-process list filtering, not
one DynamoDB round trip per golfer.
"""
import logging
from collections import defaultdict

from library.features.pga import (
    DEFAULT_COURSE_HISTORY_WINDOW,
    DEFAULT_ROLLING_WINDOW,
    build_cup_event_features,
    build_cutline_event_features,
    build_golfer_event_features,
    build_match_event_features,
    build_round_event_features,
)
from library.schema.keys import event_key as build_event_key
from library.storage.pga_season_stats import resolve_season_stats

logger = logging.getLogger("pga-predict")

SPORT = "pga"

# Statuses with nothing left to project a further round for. MDF ("made
# cut did not finish") is included: a mid-round-3 withdrawal made the cut
# but has no rounds left to play either.
_ELIMINATED_STATUSES = {"cut", "withdrawn", "made_cut_did_not_finish"}


class EventNotFoundError(Exception):
    """No stored event exists for the requested event_id."""


class MalformedEventError(Exception):
    """The stored event doesn't match the shape this function requires
    (wrong event_type, or missing a role/roster it needs)."""


def _golfer_field_events(storage, sport: str, events: list[dict], entity_id: str, before_date: str) -> list[dict]:
    """Every FIELD (stroke-play) event this golfer has played, most-
    recent-first, strictly before before_date. Filters to
    event_type=="field" since a match_play event's own participant can
    have this golfer's id as its entity_id too, and a match-play result
    (won/halved/margin) isn't a stroke score -- it must never leak into
    rolling_golfer_averages' history."""
    team_events = storage.get_team_events(sport, entity_id, before_date=before_date, events=events)
    return [e for e in team_events if e.get("event_type") == "field"]


def _golfer_result(event: dict, entity_id: str) -> dict:
    participant = next((p for p in event.get("participants", []) if p.get("entity_id") == entity_id), None)
    return (participant or {}).get("result") or {}


def _results_from_events(field_events: list[dict], entity_id: str, window: int) -> list[dict]:
    return [_golfer_result(event, entity_id) for event in field_events[:window]]


def _prior_same_round_results(field_events: list[dict], entity_id: str, round_number: int, window: int) -> list[dict]:
    """This golfer's own past rounds specifically at round_number, across
    different tournaments -- same shape rolling_round_averages expects.
    field_events is already field-only history, most-recent-first; this
    adds the per-round filter on top."""
    results = []
    for event in field_events:
        if len(results) >= window:
            break
        round_entry = next(
            (r for r in _golfer_result(event, entity_id).get("rounds", []) if r.get("round") == round_number), None,
        )
        if round_entry is not None:
            results.append(round_entry)
    return results


def applicable_rounds(participant: dict) -> list[int]:
    """Every remaining round (1-4) this golfer still needs a live
    projection for, or [] if eliminated (see _ELIMINATED_STATUSES) or
    already finished round 4. Returns every round after the last played
    one, not just the immediate next one, so the ROUND 1-4 breakdown has
    a projection for every round that hasn't happened yet."""
    result = participant.get("result") or {}
    if result.get("status") in _ELIMINATED_STATUSES:
        return []
    played = {r["round"] for r in result.get("rounds", [])}
    next_round = max(played, default=0) + 1
    return [r for r in range(next_round, 5)]


def _golfer_prior_history(
    storage, sport: str, history_events: list[dict], entity_id: str, before_date: str,
    window: int, course_id: str | None, course_window: int, snapshots: list[dict],
) -> tuple[list[dict], list[dict] | None, dict | None]:
    """(prior_results, course_results, season_stats) -- every rolling-
    history input build_golfer_event_features needs for one golfer,
    independent of whether it has a real participant row on the event
    being scored. Shared by build_live_field_features (a known field) and
    build_projected_field_features (a projected field, no real
    participant row)."""
    field_events = _golfer_field_events(storage, sport, history_events, entity_id, before_date)
    prior_results = _results_from_events(field_events, entity_id, window)

    course_results = None
    if course_id is not None:
        course_events = [e for e in field_events if e.get("course_id") == course_id]
        course_results = _results_from_events(course_events, entity_id, course_window)

    season_stats = resolve_season_stats(snapshots, entity_id, before_date) if snapshots else None
    return prior_results, course_results, season_stats


def build_live_field_features(
    storage, sport: str, event_id: str,
    window: int = DEFAULT_ROLLING_WINDOW, course_window: int = DEFAULT_COURSE_HISTORY_WINDOW,
    season_stat_snapshots: list[dict] | None = None,
) -> dict:
    """Returns {"event": event, "golfer_rows": {entity_id: {"golfer": row,
    "rounds": {round_number: row}}}, "cutline_row": row}. `rounds` only
    ever has 0 or 1 entries -- see applicable_rounds. Raises
    EventNotFoundError if no stored event exists for event_id,
    MalformedEventError if it isn't a "field" (stroke-play) event."""
    event = storage.get_event(build_event_key(sport, event_id))
    if event is None:
        raise EventNotFoundError(f"No stored PGA event for {event_id!r}")
    if event.get("event_type") != "field":
        raise MalformedEventError(f"Event {event_id!r} is event_type {event.get('event_type')!r}, not 'field'")

    participants = event.get("participants", [])
    before_date = event["event_date"]
    course_id = event.get("course_id")
    snapshots = season_stat_snapshots or []

    # One fetch, reused for every golfer's history and the course-level
    # cut-score history below.
    history_events = storage.get_all_events(sport)

    golfer_rows = {}
    for participant in participants:
        entity_id = participant["entity_id"]
        field_events = _golfer_field_events(storage, sport, history_events, entity_id, before_date)
        prior_results, course_results, season_stats = _golfer_prior_history(
            storage, sport, history_events, entity_id, before_date, window, course_id, course_window, snapshots,
        )

        # This golfer's already-played rounds this tournament, live off
        # the current stored result -- what makes a round-completion
        # recompute (rounds_fingerprint) actually change PROJ/top-10%/
        # top-5% instead of reproducing the pre-tournament feature vector.
        rounds_so_far = (participant.get("result") or {}).get("rounds", [])

        golfer_row = build_golfer_event_features(
            event, participant, prior_results, window, course_results, course_window, season_stats, rounds_so_far,
        )

        round_rows = {}
        for round_number in applicable_rounds(participant):
            prior_same_round = _prior_same_round_results(field_events, entity_id, round_number, window)
            # round_result is a stub, not a real played round -- this
            # round hasn't happened yet. build_round_event_features only
            # reads round_result["round"] and the training-only
            # score_to_par label, which model_loader.predict never uses.
            round_rows[round_number] = build_round_event_features(
                event, participant, {"round": round_number}, prior_results, prior_same_round, window,
            )

        golfer_rows[entity_id] = {"golfer": golfer_row, "rounds": round_rows}

    prior_course_cut_scores = None
    if course_id is not None:
        # Tournament-grain, not golfer-scoped -- every past field event
        # at this course_id contributes its own cut_score.
        past_at_course = [
            e for e in history_events
            if e.get("event_type") == "field" and e.get("course_id") == course_id
            and e.get("event_date", "") < before_date and e.get("cut_score") is not None
        ]
        past_at_course.sort(key=lambda e: e.get("event_date", ""), reverse=True)
        prior_course_cut_scores = [e["cut_score"] for e in past_at_course[:course_window]]

    cutline_row = build_cutline_event_features(event, prior_course_cut_scores, course_window)

    return {"event": event, "golfer_rows": golfer_rows, "cutline_row": cutline_row}


def build_projected_field_features(
    storage, sport: str, event: dict, golfer_ids: list[str],
    window: int = DEFAULT_ROLLING_WINDOW, course_window: int = DEFAULT_COURSE_HISTORY_WINDOW,
    season_stat_snapshots: list[dict] | None = None,
    history_events: list[dict] | None = None,
) -> dict[str, dict]:
    """{entity_id: golfer_row} for a PROJECTED field -- keyed off an
    externally-supplied golfer_ids list instead of trusting `event`'s
    stored participants, which for a future event is empty/sparse.

    Narrower than build_live_field_features: no round-level rows and no
    cutline_row. Every golfer gets a bare {"entity_id": entity_id}
    stand-in for a real participant row.

    history_events: pass the caller's own already-fetched
    storage.get_all_events(sport) when scoring many remaining events in
    one run to avoid re-fetching per event."""
    before_date = event["event_date"]
    course_id = event.get("course_id")
    snapshots = season_stat_snapshots or []
    all_events = history_events if history_events is not None else storage.get_all_events(sport)

    golfer_rows = {}
    for entity_id in golfer_ids:
        prior_results, course_results, season_stats = _golfer_prior_history(
            storage, sport, all_events, entity_id, before_date, window, course_id, course_window, snapshots,
        )
        golfer_rows[entity_id] = build_golfer_event_features(
            event, {"entity_id": entity_id}, prior_results, window, course_results, course_window, season_stats,
        )
    return golfer_rows


def _side_prior_results(storage, sport: str, history_events: list[dict], golfer_ids, before_date: str, window: int) -> dict[str, list[dict]]:
    return {
        gid: _results_from_events(_golfer_field_events(storage, sport, history_events, gid, before_date), gid, window)
        for gid in golfer_ids
    }


def build_live_match_features(storage, sport: str, event_id: str, window: int = DEFAULT_ROLLING_WINDOW) -> dict:
    """Returns {"event": event, "features": row} for one individual
    Ryder/Presidents Cup match or WGC match play bracket match. Raises
    EventNotFoundError/MalformedEventError."""
    event = storage.get_event(build_event_key(sport, event_id))
    if event is None:
        raise EventNotFoundError(f"No stored PGA event for {event_id!r}")
    if event.get("event_type") != "match_play":
        raise MalformedEventError(f"Event {event_id!r} is event_type {event.get('event_type')!r}, not 'match_play'")

    participants = event.get("participants", [])
    home = next((p for p in participants if p.get("role") == "home"), None)
    away = next((p for p in participants if p.get("role") == "away"), None)
    if home is None or away is None:
        raise MalformedEventError(f"Event {event_id!r} is missing a home/away participant")

    before_date = event["event_date"]
    history_events = storage.get_all_events(sport)

    home_prior = _side_prior_results(storage, sport, history_events, home.get("golfer_entity_ids", []), before_date, window)
    away_prior = _side_prior_results(storage, sport, history_events, away.get("golfer_entity_ids", []), before_date, window)

    return {"event": event, "features": build_match_event_features(event, home_prior, away_prior, window)}


def build_live_cup_features(storage, sport: str, event_id: str, window: int = DEFAULT_ROLLING_WINDOW) -> dict:
    """Returns {"event": event, "features": row} for a Ryder/Presidents
    Cup's own team result. Raises MalformedEventError if no match_play
    session for this Cup exists yet -- a Cup's roster can only be derived
    by scanning its match_play events' golfer_entity_ids (a Cup event's
    participants carry only the two teams' point totals), so predicting a
    Cup's outcome before any session is announced isn't supported."""
    event = storage.get_event(build_event_key(sport, event_id))
    if event is None:
        raise EventNotFoundError(f"No stored PGA event for {event_id!r}")
    if event.get("event_type") != "cup":
        raise MalformedEventError(f"Event {event_id!r} is event_type {event.get('event_type')!r}, not 'cup'")

    participants = event.get("participants", [])
    home = next((p for p in participants if p.get("role") == "home"), None)
    away = next((p for p in participants if p.get("role") == "away"), None)
    if home is None or away is None:
        raise MalformedEventError(f"Event {event_id!r} is missing a home/away participant")

    history_events = storage.get_all_events(sport)
    match_events = [
        e for e in history_events
        if e.get("event_type") == "match_play" and e.get("parent_event_id") == event["event_id"]
    ]
    if not match_events:
        raise MalformedEventError(f"Cup {event_id!r} has no match_play sessions yet -- roster can't be resolved")

    roster: dict[str, set] = defaultdict(set)
    for match_event in match_events:
        for participant in match_event.get("participants", []):
            role = participant.get("role")
            if role is None:
                continue
            roster[role].update(participant.get("golfer_entity_ids", []))

    before_date = event["event_date"]
    home_prior = _side_prior_results(storage, sport, history_events, roster.get("home", set()), before_date, window)
    away_prior = _side_prior_results(storage, sport, history_events, roster.get("away", set()), before_date, window)

    return {"event": event, "features": build_cup_event_features(event, home_prior, away_prior, window)}
