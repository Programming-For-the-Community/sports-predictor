"""
F1 (field-event) feature computation. Mirrors library/features/pga.py's
own shape (a driver's own entity IS the participant, same as a golfer --
no team/opponent concept for a driver's OWN rolling history), with two
genuinely new blocks PGA has no analog for at all: a constructor's own
rolling form (rolling_constructor_averages' own docstring), since a
driver's result depends heavily on car competitiveness, a signal that
belongs to the CONSTRUCTOR, not the driver; and a rolling QUALIFYING
pace block (rolling_qualifying_pace), a genuinely different skill signal
from race-day rolling form.

Sprint races (build_sprint_event_features) get their OWN feature
builder with their own separate rolling history, not folded into the
main race's rolling_driver_averages -- see library/normalize/f1.py's
sprint_result_to_event_item docstring for why.
"""

DEFAULT_ROLLING_WINDOW = 5
# Circuit-fit history is capped by count of past appearances, not
# calendar recency -- a circuit recurs roughly once a year, so 5 here
# means "last 5 years at this circuit," a longer span than
# DEFAULT_ROLLING_WINDOW's "last 5 starts." Kept as its own constant so
# the two can be tuned independently, same precedent
# library/features/pga.py's DEFAULT_COURSE_HISTORY_WINDOW sets.
DEFAULT_CIRCUIT_HISTORY_WINDOW = 5


def _as_number(value):
    """Coerces to int/float, or None for anything else. Applied to every
    value in this module that reaches a Parquet row straight from a
    stored DynamoDB result dict, without going through a rolling_*_
    averages helper first -- one mixed-type column fails pyarrow's
    entire dataset write, not just that one row."""
    return value if isinstance(value, (int, float)) else None


def rolling_driver_averages(driver_results: list[dict], window: int = DEFAULT_ROLLING_WINDOW) -> dict:
    """driver_results: a driver's own past participants[].result dicts
    (library/normalize/f1.py's _driver_result shape), most recent first,
    not including the race being scored.

    podium_rate/top_10_rate/dnf_rate are divided by the number of starts
    in the window, not just the classified finishes -- a DNF counts
    against making the podium, not excluded from the denominator.
    avg_finish_position/avg_grid_position/avg_points average only over
    rows that have a real value -- finish_position is None for a
    non-classified result (see library/normalize/f1.py's map_status),
    same "None means no real value, not 0" discipline
    library/features/pga.py's rolling_golfer_averages uses.

    Every value is None (not 0) when the window has no qualifying rows."""
    windowed = driver_results[:window]
    starts = len(windowed)

    finished = [r for r in windowed if isinstance(r.get("finish_position"), (int, float))]
    finish_positions = [r["finish_position"] for r in finished]
    grid_positions = [r["grid_position"] for r in windowed if isinstance(r.get("grid_position"), (int, float))]
    points_values = [r["points"] for r in windowed if isinstance(r.get("points"), (int, float))]
    dnf_count = sum(1 for r in windowed if r.get("status") == "dnf")

    return {
        "avg_finish_position": sum(finish_positions) / len(finish_positions) if finish_positions else None,
        "best_finish_position": min(finish_positions) if finish_positions else None,
        "avg_grid_position": sum(grid_positions) / len(grid_positions) if grid_positions else None,
        "avg_points": sum(points_values) / len(points_values) if points_values else None,
        "podium_rate": sum(1 for p in finish_positions if p <= 3) / starts if starts else None,
        "top_10_rate": sum(1 for p in finish_positions if p <= 10) / starts if starts else None,
        "dnf_rate": dnf_count / starts if starts else None,
        "starts": starts,
    }


def rolling_constructor_averages(constructor_results: list[dict], window: int = DEFAULT_ROLLING_WINDOW) -> dict:
    """constructor_results: this constructor's own past result dicts,
    POOLED across both of its drivers and interleaved most-recent-first
    (the caller's job -- see feature-engineering/f1/build_dataset.py) --
    a constructor's rolling form is the CAR's competitiveness, not one
    driver's skill, which is exactly why car-competitiveness needs a
    block of its own here at all: nothing about one driver's own rolling
    history (rolling_driver_averages above) tells a model how good their
    teammate's identical car has been running. Deliberately reuses
    rolling_driver_averages' own math over that pooled 2-driver history
    rather than a separate implementation -- window here counts RESULT
    ROWS (roughly window/2 races, since 2 drivers contribute per race),
    not races directly, since interleaving is the caller's
    responsibility, not this function's."""
    return rolling_driver_averages(constructor_results, window)


def rolling_qualifying_pace(qualifying_results: list[dict], window: int = DEFAULT_ROLLING_WINDOW) -> dict:
    """qualifying_results: a driver's own past participants[].result[
    "qualifying"] dicts (library/normalize/f1.py's qualifying_payload_
    to_results shape), most recent first, not including the qualifying
    session being scored. Callers are expected to have already filtered
    out a race with no qualifying data at all (None) before passing its
    history in here -- see feature-engineering/f1/build_dataset.py.

    avg_gap_to_pole_seconds is a sharper, pace-based analog of
    rolling_driver_averages' own avg_grid_position -- a genuinely
    different signal (how FAST, not just where they started, which can
    diverge from raw pace via grid penalties). Reused unchanged for a
    constructor's own pooled qualifying pace (both drivers' qualifying
    history interleaved by the caller), same "pool, don't average
    separately" pattern rolling_constructor_averages already
    establishes for race-day form."""
    windowed = qualifying_results[:window]
    sessions = len(windowed)
    gaps = [r["gap_to_pole_seconds"] for r in windowed if isinstance(r.get("gap_to_pole_seconds"), (int, float))]
    positions = [r["position"] for r in windowed if isinstance(r.get("position"), (int, float))]
    return {
        "avg_gap_to_pole_seconds": sum(gaps) / len(gaps) if gaps else None,
        "avg_qualifying_position": sum(positions) / len(positions) if positions else None,
        "best_qualifying_position": min(positions) if positions else None,
        "qualifying_sessions": sessions,
    }


def build_driver_event_features(
    event: dict,
    participant: dict,
    prior_results: list[dict],
    window: int = DEFAULT_ROLLING_WINDOW,
    circuit_results: list[dict] | None = None,
    circuit_window: int = DEFAULT_CIRCUIT_HISTORY_WINDOW,
    constructor_results: list[dict] | None = None,
    constructor_window: int = DEFAULT_ROLLING_WINDOW,
    qualifying_history: list[dict] | None = None,
    constructor_qualifying_history: list[dict] | None = None,
) -> dict:
    """One training row: this driver's rolling form (from prior_results)
    plus this event's own context (grid_position, field_size, circuit_id),
    a circuit-fit block (from circuit_results, this driver's past results
    at this event's circuit_id), a constructor-form block (from
    constructor_results, the CAR's own recent pooled performance across
    both its drivers -- see rolling_constructor_averages), a rolling
    qualifying-pace block (from qualifying_history, this driver's own
    past gap-to-pole/qualifying-position history -- see rolling_
    qualifying_pace) plus its constructor-pooled counterpart
    (constructor_qualifying_history), and every label this shared
    dataset trains toward (win, podium, DNF, the continuous finish-
    position a "predicted running order" ranking uses at serving time,
    and the continuous qualifying-position the standalone qualifying
    model trains toward).

    circuit_results/constructor_results/qualifying_history/constructor_
    qualifying_history all default to None (not []) so a caller that
    hasn't resolved that history yet still gets every circuit_*/
    constructor_*/qualifying_*/constructor_qualifying_* column as an
    explicit missing value, rather than a row whose column set differs
    from every other row's -- same precedent library/features/pga.py's
    build_golfer_event_features sets for its own course_results/
    season_stats defaults.

    label_qualifying_position comes from participant["result"][
    "qualifying"]["position"] (library/normalize/f1.py's merge_
    qualifying_into_event) -- None (excluded from training) whenever
    qualifying hasn't been merged into this event yet, same "None means
    excluded, not a bad zero" treatment label_finish_position already
    gets for a non-classified race result."""
    result = participant.get("result") or {}
    finish_position = _as_number(result.get("finish_position"))
    status = result.get("status")
    qualifying = result.get("qualifying") or {}

    row = {
        "event_key": event["event_key"],
        "entity_id": participant["entity_id"],
        "constructor_entity_id": participant.get("constructor_entity_id"),
        "event_date": event["event_date"],
        "circuit_id": event.get("circuit_id"),
        "grid_position": _as_number(result.get("grid_position")),
        "field_size": len(event.get("participants", [])),
        "label_win": 1 if finish_position == 1 else 0,
        "label_podium": 1 if finish_position is not None and finish_position <= 3 else 0,
        # None (excluded from training) for a non-classified result --
        # there is no real finishing position to regress toward for a
        # DNF/DSQ/DNS.
        "label_finish_position": finish_position,
        "label_dnf": 1 if status == "dnf" else 0,
        "label_qualifying_position": _as_number(qualifying.get("position")),
    }
    row.update(rolling_driver_averages(prior_results, window))
    row.update({f"circuit_{key}": value for key, value in rolling_driver_averages(circuit_results or [], circuit_window).items()})
    row.update({f"constructor_{key}": value for key, value in rolling_constructor_averages(constructor_results or [], constructor_window).items()})
    row.update({f"qualifying_{key}": value for key, value in rolling_qualifying_pace(qualifying_history or [], window).items()})
    row.update({
        f"constructor_qualifying_{key}": value
        for key, value in rolling_qualifying_pace(constructor_qualifying_history or [], constructor_window).items()
    })
    return row


def _sum_forms(per_driver_dicts: list[dict]) -> dict:
    """Element-wise SUM (not average) across each of a constructor's
    drivers' own rolling_driver_averages output -- deliberately different
    from library/features/pga.py's own _average_side (which averages a
    match-play side's golfers): matches library/features/f1_points.py's
    own constructor_points, which is a real sum of both drivers' points
    each race, not a mean, so the feature a constructor win-probability
    model sees should reflect the same "two threats add up" reality, not
    average a strong driver down toward a weak teammate. A None
    contributes 0 rather than being excluded, so one driver's real value
    still counts in full even when the other's own window is empty (a
    mid-season driver swap, or a rookie teammate with no prior starts).
    Empty input returns {}, not a dict of zeros."""
    if not per_driver_dicts:
        return {}
    keys = per_driver_dicts[0].keys()
    return {key: sum(d.get(key) or 0 for d in per_driver_dicts) for key in keys}


def build_constructor_event_features(
    event: dict,
    constructor_entity_id: str,
    driver_participants: list[dict],
    prior_results_by_driver: dict[str, list[dict]],
    window: int = DEFAULT_ROLLING_WINDOW,
) -> dict:
    """One training row per (constructor, race) for the constructor
    win-probability model -- driver_participants is this constructor's
    own participants[] entries in THIS race (1 or 2, ignoring every
    other constructor's drivers); prior_results_by_driver maps each of
    those drivers' entity_id to their own prior results, most recent
    first, not including this race.

    label_win is 1 if EITHER of this constructor's drivers won the race
    -- a constructor "wins" when either of its cars crosses the line
    first, not by some combined-score threshold."""
    driver_forms = [
        rolling_driver_averages(prior_results_by_driver.get(p["entity_id"], []), window)
        for p in driver_participants
    ]
    finish_positions = [
        fp for p in driver_participants
        if (fp := _as_number((p.get("result") or {}).get("finish_position"))) is not None
    ]

    row = {
        "event_key": event["event_key"],
        "entity_id": constructor_entity_id,
        "event_date": event["event_date"],
        "circuit_id": event.get("circuit_id"),
        "label_win": 1 if finish_positions and min(finish_positions) == 1 else 0,
    }
    row.update(_sum_forms(driver_forms))
    return row


def build_sprint_event_features(
    event: dict,
    participant: dict,
    prior_sprint_results: list[dict],
    window: int = DEFAULT_ROLLING_WINDOW,
    constructor_sprint_results: list[dict] | None = None,
    constructor_window: int = DEFAULT_ROLLING_WINDOW,
) -> dict:
    """One training row per (driver, Sprint race) -- deliberately its OWN
    rolling history (prior_sprint_results/constructor_sprint_results),
    tracked entirely separately from the main race's rolling_driver_
    averages -- see library/normalize/f1.py's sprint_result_to_event_item
    docstring for why Sprint isn't blended into main-race history.

    No circuit-fit block (unlike build_driver_event_features) -- a
    Sprint weekend only rotates across a handful of DIFFERENT circuits
    each season (not the full calendar, and not necessarily the same
    circuits year to year), so a circuit-specific Sprint history would
    be sparse to the point of being noise rather than signal.

    No qualifying-pace block either -- Jolpica has no separate Sprint
    Qualifying/Sprint Shootout results endpoint at all, so there is no
    real pace data for that session to build a rolling block from.

    label_sprint_grid_position is this driver's real STARTING grid for
    the Sprint race itself, taken straight from participant["result"][
    "grid_position"] the same way the main race's own grid_position is
    populated -- this doubles as the closest available "Sprint
    qualifying" target: a driver's Sprint grid position is the only
    trace that session's outcome leaves in Jolpica's data at all, with
    no lap-time/pace signal behind it the way the MAIN qualifying model
    has via gap_to_pole_seconds."""
    result = participant.get("result") or {}
    finish_position = _as_number(result.get("finish_position"))
    status = result.get("status")

    row = {
        "event_key": event["event_key"],
        "entity_id": participant["entity_id"],
        "constructor_entity_id": participant.get("constructor_entity_id"),
        "event_date": event["event_date"],
        "circuit_id": event.get("circuit_id"),
        "field_size": len(event.get("participants", [])),
        "label_win": 1 if finish_position == 1 else 0,
        "label_podium": 1 if finish_position is not None and finish_position <= 3 else 0,
        "label_sprint_grid_position": _as_number(result.get("grid_position")),
        "label_dnf": 1 if status == "dnf" else 0,
    }
    row.update(rolling_driver_averages(prior_sprint_results, window))
    row.update({
        f"constructor_{key}": value
        for key, value in rolling_constructor_averages(constructor_sprint_results or [], constructor_window).items()
    })
    return row
