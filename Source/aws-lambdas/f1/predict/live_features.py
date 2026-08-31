"""
Live (serving-time) F1 feature building -- re-derives every driver's and
constructor's own rolling history from current DynamoDB state and feeds it
through the same pure library/features/f1.py functions training uses,
mirroring aws-lambdas/pga/predict/live_features.py's own "reuse the
training-time builder, re-derive its inputs live" pattern. The history
walk here mirrors feature-engineering/f1/build_dataset.py's own
incremental-pooling logic (see _constructor_pooled_results/_constructor_
pooled_qualifying below), just resolving ONE event's own history on
demand instead of growing every event's row in one chronological pass.

One storage.get_all_events(sport) call up front gives every driver's/
constructor's history in memory, so scoring a race's field is in-process
list filtering, not one DynamoDB round trip per driver.
"""
import logging
from collections import defaultdict

from library.features.f1 import (
    DEFAULT_CIRCUIT_HISTORY_WINDOW,
    DEFAULT_ROLLING_WINDOW,
    build_constructor_event_features,
    build_driver_event_features,
    build_sprint_event_features,
)
from library.schema.keys import event_key as build_event_key

logger = logging.getLogger("f1-predict")

SPORT = "f1"


class EventNotFoundError(Exception):
    """No stored event exists for the requested event_id."""


class MalformedEventError(Exception):
    """The stored event doesn't match the shape this function requires
    (wrong event_type)."""


def _events_before(all_events: list[dict], event_type: str, before_date: str) -> list[dict]:
    """Every event of event_type ("field" or "sprint"), strictly before
    before_date, most-recent-first -- excludes a "scheduled" stub (no
    real result to fold into a rolling history) implicitly, since a
    scheduled event's own participants list is always empty (library/
    normalize/f1.py's schedule_payload_to_scheduled_events)."""
    filtered = [e for e in all_events if e.get("event_type") == event_type and e.get("event_date", "") < before_date]
    filtered.sort(key=lambda e: e.get("event_date", ""), reverse=True)
    return filtered


def _driver_results(events_most_recent_first: list[dict], entity_id: str, window: int) -> list[dict]:
    results = []
    for event in events_most_recent_first:
        if len(results) >= window:
            break
        participant = next((p for p in event.get("participants", []) if p.get("entity_id") == entity_id), None)
        if participant is not None:
            results.append(participant.get("result") or {})
    return results


def _driver_qualifying_history(events_most_recent_first: list[dict], entity_id: str, window: int) -> list[dict]:
    """This driver's own past qualifying dicts, most-recent-first,
    skipping any race whose qualifying was never merged in
    (result["qualifying"] is None) -- same "don't pollute the history
    with a placeholder" discipline feature-engineering/f1/build_dataset.
    py's own qualifying_history uses."""
    results = []
    for event in events_most_recent_first:
        if len(results) >= window:
            break
        participant = next((p for p in event.get("participants", []) if p.get("entity_id") == entity_id), None)
        if participant is None:
            continue
        qualifying = (participant.get("result") or {}).get("qualifying")
        if qualifying is not None:
            results.append(qualifying)
    return results


def _constructor_pooled_results(events_most_recent_first: list[dict], constructor_id: str, window: int) -> list[dict]:
    """This constructor's own past result dicts, POOLED across both of
    its drivers -- see library/features/f1.py's rolling_constructor_
    averages docstring for why this needs to be a pooled history, not a
    per-driver one. Walks most-recent-event-first; within a single event
    both drivers (if both belong to this constructor) are taken in
    participants' own stored order -- window counts RESULT ROWS, not
    races, same convention feature-engineering/f1/build_dataset.py's own
    constructor_history uses."""
    results = []
    for event in events_most_recent_first:
        for participant in event.get("participants", []):
            if participant.get("constructor_entity_id") == constructor_id:
                results.append(participant.get("result") or {})
                if len(results) >= window:
                    return results
    return results


def _constructor_pooled_qualifying(events_most_recent_first: list[dict], constructor_id: str, window: int) -> list[dict]:
    results = []
    for event in events_most_recent_first:
        for participant in event.get("participants", []):
            if participant.get("constructor_entity_id") == constructor_id:
                qualifying = (participant.get("result") or {}).get("qualifying")
                if qualifying is not None:
                    results.append(qualifying)
                    if len(results) >= window:
                        return results
    return results


def _with_grid_from_qualifying(participant: dict) -> dict:
    """Live-serving-only adjustment: the real post-qualifying starting
    GRID isn't independently knowable from Jolpica at all until the RACE
    itself has been run -- `result.grid_position` (library/normalize/
    f1.py's own _driver_result) is populated from the SAME results
    payload as finish_position, not from qualifying. That's correct for
    a completed historical race (both land together, real training data
    either way), but for a race that hasn't happened yet, grid_position
    would stay None even AFTER real qualifying happens Saturday --
    silently dropping build_driver_event_features' own single strongest
    feature to missing for the one window (post-qualifying, pre-race)
    where a live prediction matters most.

    Substitutes this weekend's own real qualifying position (available a
    full day earlier, via merge_qualifying_into_event) as a stand-in
    for grid_position whenever the real one isn't known yet -- qualifying
    position and the real starting grid coincide in the overwhelming
    majority of cases (grid penalties are the rare exception, and Jolpica
    exposes no separate signal for those anyway), making this the best
    real information available rather than leaving the feature missing.
    Only ever applied here, at serving time -- library/features/f1.py's
    own build_driver_event_features and feature-engineering/f1/
    build_dataset.py both keep using the real post-race grid_position
    unchanged for training, where it's always genuinely available."""
    result = participant.get("result") or {}
    if result.get("grid_position") is not None:
        return participant
    qualifying_position = (result.get("qualifying") or {}).get("position")
    if qualifying_position is None:
        return participant
    return {**participant, "result": {**result, "grid_position": qualifying_position}}


def current_roster(storage, sport: str, all_events: list[dict] | None = None) -> tuple[list[str], dict[str, str]]:
    """(driver_ids, driver_to_constructor) resolved from the most
    recently COMPLETED "field" race's own real participants -- the same
    "current lineup" resolution aws-lambdas/f1/predict/season_projection.
    py's own _season_standings_inputs already uses for its remaining-race
    projection. Shared here so build_live_field_features (below) can
    score a "scheduled" stub event (library/normalize/f1.py's schedule_
    payload_to_scheduled_events -- always EMPTY participants, since
    Jolpica has no pre-race entry-list endpoint at all, unlike ESPN's
    real pre-tournament field PGA's own schedule-sync gets) against a
    real field instead of coming back with nothing to show at all."""
    events = all_events if all_events is not None else storage.get_all_events(sport, status="completed")
    field_events_desc = sorted(
        (e for e in events if e.get("event_type") == "field"), key=lambda e: e.get("event_date", ""), reverse=True,
    )
    participants = field_events_desc[0].get("participants", []) if field_events_desc else []
    driver_ids = [p["entity_id"] for p in participants]
    driver_to_constructor = {p["entity_id"]: p["constructor_entity_id"] for p in participants if p.get("constructor_entity_id") is not None}
    return driver_ids, driver_to_constructor


def _projected_constructor_rows(
    storage, sport: str, event: dict, driver_ids: list[str], driver_to_constructor: dict[str, str],
    window: int, field_events_before: list[dict],
) -> dict[str, dict]:
    """Constructor rows for a PROJECTED field -- same grouping
    build_live_field_features' own by_constructor block does for a real
    field, just built from the projected roster's own driver_ids/
    driver_to_constructor instead of the event's own (empty) participants."""
    by_constructor: dict[str, list[dict]] = defaultdict(list)
    for entity_id in driver_ids:
        constructor_id = driver_to_constructor.get(entity_id)
        if constructor_id is not None:
            by_constructor[constructor_id].append({"entity_id": entity_id, "constructor_entity_id": constructor_id, "result": {}})

    constructor_rows = {}
    for constructor_id, driver_participants in by_constructor.items():
        prior_by_driver = {
            p["entity_id"]: _driver_results(field_events_before, p["entity_id"], window) for p in driver_participants
        }
        constructor_rows[constructor_id] = build_constructor_event_features(
            event, constructor_id, driver_participants, prior_by_driver, window,
        )
    return constructor_rows


def build_live_field_features(
    storage, sport: str, event_id: str,
    window: int = DEFAULT_ROLLING_WINDOW, circuit_window: int = DEFAULT_CIRCUIT_HISTORY_WINDOW,
) -> dict:
    """Returns {"event": event, "driver_rows": {entity_id: row},
    "constructor_rows": {constructor_entity_id: row}}. Raises
    EventNotFoundError if no stored event exists for event_id,
    MalformedEventError if it isn't a "field" (main race) event.

    A "scheduled" stub event (no real participants yet -- see library/
    normalize/f1.py's schedule_payload_to_scheduled_events) is scored
    against the CURRENT roster (current_roster, above) instead of coming
    back empty -- same "project the current lineup onto a not-yet-run
    race" idea season_projection.py already uses for the season
    simulation, just applied here to a single on-demand event request
    too. A completed OR already-underway (real, non-empty participants)
    event is scored against its own real field, unchanged."""
    event = storage.get_event(build_event_key(sport, event_id))
    if event is None:
        raise EventNotFoundError(f"No stored F1 event for {event_id!r}")
    if event.get("event_type") != "field":
        raise MalformedEventError(f"Event {event_id!r} is event_type {event.get('event_type')!r}, not 'field'")

    participants = event.get("participants", [])
    before_date = event["event_date"]
    circuit_id = event.get("circuit_id")

    all_events = storage.get_all_events(sport)
    field_events_before = _events_before(all_events, "field", before_date)
    circuit_events_before = [e for e in field_events_before if circuit_id is not None and e.get("circuit_id") == circuit_id]

    if not participants:
        driver_ids, driver_to_constructor = current_roster(storage, sport, all_events=[e for e in all_events if e.get("status") == "completed"])
        driver_rows = build_projected_field_features(
            storage, sport, event, driver_ids, driver_to_constructor, window, circuit_window, all_events=all_events,
        )
        constructor_rows = _projected_constructor_rows(storage, sport, event, driver_ids, driver_to_constructor, window, field_events_before)
        return {"event": event, "driver_rows": driver_rows, "constructor_rows": constructor_rows}

    driver_rows = {}
    for raw_participant in participants:
        participant = _with_grid_from_qualifying(raw_participant)
        entity_id = participant["entity_id"]
        constructor_id = participant.get("constructor_entity_id")

        prior_results = _driver_results(field_events_before, entity_id, window)
        circuit_results = _driver_results(circuit_events_before, entity_id, circuit_window) if circuit_id is not None else None
        constructor_results = (
            _constructor_pooled_results(field_events_before, constructor_id, window) if constructor_id is not None else None
        )
        qualifying_history = _driver_qualifying_history(field_events_before, entity_id, window)
        constructor_qualifying = (
            _constructor_pooled_qualifying(field_events_before, constructor_id, window) if constructor_id is not None else None
        )

        driver_rows[entity_id] = build_driver_event_features(
            event, participant, prior_results, window, circuit_results, circuit_window,
            constructor_results, window, qualifying_history, constructor_qualifying,
        )

    by_constructor: dict[str, list[dict]] = defaultdict(list)
    for raw_participant in participants:
        constructor_id = raw_participant.get("constructor_entity_id")
        if constructor_id is not None:
            by_constructor[constructor_id].append(_with_grid_from_qualifying(raw_participant))

    constructor_rows = {}
    for constructor_id, driver_participants in by_constructor.items():
        prior_by_driver = {
            p["entity_id"]: _driver_results(field_events_before, p["entity_id"], window) for p in driver_participants
        }
        constructor_rows[constructor_id] = build_constructor_event_features(
            event, constructor_id, driver_participants, prior_by_driver, window,
        )

    return {"event": event, "driver_rows": driver_rows, "constructor_rows": constructor_rows}


def build_live_sprint_features(storage, sport: str, event_id: str, window: int = DEFAULT_ROLLING_WINDOW) -> dict:
    """Returns {"event": event, "driver_rows": {entity_id: row}}. Raises
    EventNotFoundError/MalformedEventError. No constructor rows and no
    grid-from-qualifying substitution -- a Sprint session has no
    qualifying data of its own at all (see library/features/f1.py's
    build_sprint_event_features docstring), so there's nothing to
    substitute from; its own real grid_position lands the same way the
    main race's does, from the Sprint's own results payload post-session."""
    event = storage.get_event(build_event_key(sport, event_id))
    if event is None:
        raise EventNotFoundError(f"No stored F1 event for {event_id!r}")
    if event.get("event_type") != "sprint":
        raise MalformedEventError(f"Event {event_id!r} is event_type {event.get('event_type')!r}, not 'sprint'")

    participants = event.get("participants", [])
    before_date = event["event_date"]

    all_events = storage.get_all_events(sport)
    sprint_events_before = _events_before(all_events, "sprint", before_date)

    driver_rows = {}
    for participant in participants:
        entity_id = participant["entity_id"]
        constructor_id = participant.get("constructor_entity_id")
        prior_results = _driver_results(sprint_events_before, entity_id, window)
        constructor_results = (
            _constructor_pooled_results(sprint_events_before, constructor_id, window) if constructor_id is not None else None
        )
        driver_rows[entity_id] = build_sprint_event_features(event, participant, prior_results, window, constructor_results, window)

    return {"event": event, "driver_rows": driver_rows}


def build_projected_field_features(
    storage, sport: str, event: dict, driver_ids: list[str], driver_to_constructor: dict[str, str],
    window: int = DEFAULT_ROLLING_WINDOW, circuit_window: int = DEFAULT_CIRCUIT_HISTORY_WINDOW,
    all_events: list[dict] | None = None,
) -> dict[str, dict]:
    """{entity_id: driver_row} for a PROJECTED (not-yet-run) race --
    keyed off an externally-supplied driver_ids list (season_projection.
    py's own current tracked_roster) rather than the event's own
    participants, which for a "scheduled" stub event (library/normalize/
    f1.py's schedule_payload_to_scheduled_events) is always empty.

    No constructor rows -- season_projection.py's own simulate_season
    derives constructor points from the simulated DRIVER points directly
    (library/features/f1_points.py's constructor_points), it never scores
    a constructor model per remaining race the way predict_field_event
    does for an actual single-race request.

    all_events: pass the caller's own already-fetched
    storage.get_all_events(sport) when scoring many remaining races in
    one run, to avoid re-fetching per race -- same precedent PGA's own
    build_projected_field_features sets."""
    before_date = event["event_date"]
    circuit_id = event.get("circuit_id")
    events = all_events if all_events is not None else storage.get_all_events(sport)
    field_events_before = _events_before(events, "field", before_date)
    circuit_events_before = [e for e in field_events_before if circuit_id is not None and e.get("circuit_id") == circuit_id]

    driver_rows = {}
    for entity_id in driver_ids:
        constructor_id = driver_to_constructor.get(entity_id)
        participant = _with_grid_from_qualifying({"entity_id": entity_id, "constructor_entity_id": constructor_id})

        prior_results = _driver_results(field_events_before, entity_id, window)
        circuit_results = _driver_results(circuit_events_before, entity_id, circuit_window) if circuit_id is not None else None
        constructor_results = (
            _constructor_pooled_results(field_events_before, constructor_id, window) if constructor_id is not None else None
        )
        qualifying_history = _driver_qualifying_history(field_events_before, entity_id, window)
        constructor_qualifying = (
            _constructor_pooled_qualifying(field_events_before, constructor_id, window) if constructor_id is not None else None
        )

        driver_rows[entity_id] = build_driver_event_features(
            event, participant, prior_results, window, circuit_results, circuit_window,
            constructor_results, window, qualifying_history, constructor_qualifying,
        )
    return driver_rows
