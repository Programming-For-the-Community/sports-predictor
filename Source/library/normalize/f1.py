"""
Formula 1 (Jolpica-F1/Ergast-compatible) normalizers -- the field-event
counterpart to library/normalize/pga.py for a driver/constructor sport.
Genuinely different from PGA in two structural ways PGA has no analog for
at all: every driver belongs to a constructor, and (see
library/features/f1_points.py) constructor points are a real sum of both
drivers' points each race -- both driver ("player") and constructor
("team") entities are written from the same race-results response below;
and a race weekend has TWO separate Jolpica endpoints (results,
qualifying) that both need combining into ONE event item, unlike PGA's
single leaderboard fetch that already carries everything.

All field names verified against real Jolpica-F1 responses (a normal
race, a DNF-heavy race with both a classified and an unclassified
retirement, a Sprint weekend, a real qualifying response including a
Q1-only-eliminated driver with no Q2/Q3 keys at all) before being
written, per this project's own "verify raw fields before feature code"
rule -- confirmed live 2026-08-30/2026-08-31 against
http://api.jolpi.ca/ergast/f1/2024/*.
"""
import logging

from library.schema.keys import entity_key, event_key

logger = logging.getLogger(__name__)

# "Finished"/"Disqualified"/"Did not start"/"Did not qualify"/"Did not
# prequalify" are the only free-text `status` strings mapped directly by
# name -- everything else (Ergast/F1 has ~138 distinct status strings
# across history: "Retired", "Lapped", "Accident", "Engine", "Gearbox",
# dozens of specific retirement reasons) is disambiguated by
# positionText instead, not enumerated here one by one -- see
# map_status's own docstring.
_STATUS_TEXT_MAP = {
    "Finished": "finished",
    "Disqualified": "dsq",
    "Did not start": "dns",
    "Did not qualify": "dns",
    "Did not prequalify": "dns",
}

# positionText's own letter-code vocabulary for a driver Ergast didn't
# assign a real classification position to (didn't cover F1's real
# >=90%-of-race-distance classification threshold) -- confirmed live
# 2026-08-30 (2024 Australian GP) for "R"; the rest ("D"/"W"/"N"/"F")
# follow the same documented Ergast convention, not yet individually
# observed live in this project's own spot-check.
_POSITION_LETTER_MAP = {
    "R": "dnf",  # retired, unclassified
    "D": "dsq",
    "W": "dns",  # withdrawn before the race
    "N": "dnf",  # not classified
    "F": "dns",  # failed to (pre)qualify
}


def map_status(status_text: str, position_text: str) -> str:
    """Public -- reused by feature engineering wherever a raw race
    result needs re-mapping outside normalize (e.g. a backfill script
    re-deriving status from a cached raw payload).

    Ergast's own `position` field is a plain classification-order
    integer for EVERY entrant, even a lap-3 retirement -- that's order of
    retirement, not a real finishing position, so `status` alone can't
    tell "finished/lapped-but-classified" apart from "retired early,
    unclassified" (both can show status "Retired"). Confirmed live,
    2026-08-30, on the 2024 Australian Grand Prix: a driver who completed
    56 of 57 laps and retired near the end still got positionText "17"
    (classified), while drivers who completed 15 laps and 3 laps got
    positionText "R" (unclassified) despite an identical status string
    "Retired" on all three. A digit positionText always means classified
    (whether the real status was "Finished," "Lapped," or a
    still-classified "Retired"); a letter code always means "didn't
    count," and _POSITION_LETTER_MAP disambiguates which flavor."""
    if status_text in _STATUS_TEXT_MAP:
        return _STATUS_TEXT_MAP[status_text]
    if (position_text or "").isdigit():
        return "classified"
    mapped = _POSITION_LETTER_MAP.get(position_text)
    if mapped is None:
        logger.warning(
            "Unmapped F1 status/positionText combo (status=%r, positionText=%r) -- falling back to dnf",
            status_text, position_text,
        )
        return "dnf"
    return mapped


def _parse_finish_position(position: str | None, position_text: str) -> int | None:
    """Only trusts Ergast's `position` when positionText confirms this
    driver was actually CLASSIFIED (a digit, not a letter code) -- see
    map_status's own docstring for why the raw `position` value alone
    isn't a real finishing position for an unclassified DNF."""
    if (position_text or "").isdigit() and position is not None:
        return int(position)
    return None


def _int_or_none(value: str | None) -> int | None:
    return int(value) if value is not None and str(value).isdigit() else None


def _driver_result(result: dict) -> dict:
    driver = result.get("Driver", {})
    constructor = result.get("Constructor", {})
    position_text = result.get("positionText", "")
    status_text = result.get("status", "")
    fastest_lap = result.get("FastestLap") or {}
    points = result.get("points")
    return {
        "entity_id": driver.get("driverId"),
        "constructor_entity_id": constructor.get("constructorId"),
        "result": {
            "finish_position": _parse_finish_position(result.get("position"), position_text),
            "grid_position": _int_or_none(result.get("grid")),
            "status": map_status(status_text, position_text),
            "points": float(points) if points is not None else 0.0,
            "fastest_lap": fastest_lap.get("rank") == "1",
            "laps_completed": _int_or_none(result.get("laps")),
        },
    }


def _event_item_from_race(race: dict, sport: str, *, results_key: str, event_type: str, event_id_suffix: str = "") -> dict:
    """The actual per-race dict builder, shared by _race_like_event_item
    below (one already-resolved race, from the results/sprint endpoints)
    and schedule_payload_to_scheduled_events further down (every race on
    a season's own calendar at once, from the schedule endpoint -- which
    carries no `results_key` at all, so `race.get(results_key, [])`
    naturally resolves to an empty participants list and status
    "scheduled" for a round that hasn't been run yet, with no special
    casing needed here)."""
    circuit = race.get("Circuit", {})
    location = circuit.get("Location", {})
    season = race.get("season")
    round_ = race.get("round")
    event_id = f"{season}-{round_}{event_id_suffix}"

    participants = [_driver_result(r) for r in race.get(results_key, [])]

    return {
        "event_key": event_key(sport, event_id),
        "event_id": event_id,
        "sport": sport,
        "event_type": event_type,
        "event_date": race.get("date"),
        "status": "completed" if participants else "scheduled",
        "participants": participants,
        "season": int(season) if season else None,
        "season_type": None,
        "week": int(round_) if round_ else None,
        "venue_indoor": False,
        "venue_name": circuit.get("circuitName"),
        "venue_city": location.get("locality"),
        "venue_state": location.get("country"),
        # circuit_id -- Jolpica's own stable circuit identifier (e.g.
        # "bahrain"), the F1 analog of library/normalize/pga.py's
        # course_id: a circuit recurs across seasons far more reliably by
        # this id than by venue_name, for library/features/f1.py's own
        # rolling per-circuit history.
        "circuit_id": circuit.get("circuitId"),
        "race_name": race.get("raceName"),
    }


def _race_like_event_item(payload: dict, sport: str, *, results_key: str, event_type: str, event_id_suffix: str = "") -> dict:
    """Shared builder for both a main race (results_key="Results",
    event_type="field") and a Sprint race (results_key="SprintResults",
    event_type="sprint") -- Jolpica returns a byte-identical per-driver
    result shape for both (position/positionText/points/grid/laps/
    status/Driver/Constructor/FastestLap, confirmed live 2026-08-30),
    just under a different top-level key -- nothing here needs to know
    which points table applied to produce `points`, only normalize
    whatever Jolpica already recorded.

    Races is always a single-element list, queried by an exact
    season+round -- same "always exactly one" guarantee
    library/http/pga.py's get_leaderboard documents for its own
    event-scoped fetch. Raises ValueError up front for an empty Races
    list (round not yet run) rather than crashing further down --
    callers (ingest) should already only call this once a round's
    results actually exist."""
    races = payload.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    if not races:
        raise ValueError(
            f"No race found in payload for {results_key!r} -- this season/round hasn't been run yet, "
            f"or isn't a real Jolpica response",
        )
    return _event_item_from_race(races[0], sport, results_key=results_key, event_type=event_type, event_id_suffix=event_id_suffix)


def race_result_to_event_item(payload: dict, sport: str) -> dict:
    """payload is JolpicaClient.get_race_results(season, round)'s raw
    response. See _race_like_event_item's own docstring."""
    return _race_like_event_item(payload, sport, results_key="Results", event_type="field")


def sprint_result_to_event_item(payload: dict, sport: str) -> dict:
    """payload is JolpicaClient.get_sprint(season, round)'s raw response
    -- same envelope shape as get_race_results, just under `SprintResults`
    instead of `Results`. Written as its OWN event (event_type "sprint",
    event_id suffixed "-sprint" so it never collides with the same
    weekend's main-race event_id) rather than folded into the main
    race's own participants -- a Sprint is a genuinely separate
    competitive session with its own real classification/points, not a
    round of the same event the way PGA folds multiple rounds into one
    tournament event.

    Deliberately kept OUT of the main race's own rolling history
    entirely (library/features/f1.py's build_sprint_event_features
    tracks its own SEPARATE rolling history) -- a ~19-lap Sprint dash is
    a different enough format from a full Grand Prix that blending the
    two would muddy both signals, mirroring PGA's own precedent of
    match_play/cup being distinct event_types from field rather than
    folded in."""
    return _race_like_event_item(payload, sport, results_key="SprintResults", event_type="sprint", event_id_suffix="-sprint")


def _driver_entities(payload: dict, sport: str, results_key: str) -> list[dict]:
    races = payload.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    if not races:
        return []
    entities = []
    seen: set[str] = set()
    for result in races[0].get(results_key, []):
        driver = result.get("Driver", {})
        driver_id = driver.get("driverId")
        if not driver_id or driver_id in seen:
            continue
        seen.add(driver_id)
        entities.append({
            "entity_key": entity_key(sport, driver_id, "player"),
            "entity_id": driver_id,
            "sport": sport,
            "entity_type": "player",
            "name": f"{driver.get('givenName', '')} {driver.get('familyName', '')}".strip(),
            "metadata": {
                "code": driver.get("code"),
                "permanent_number": driver.get("permanentNumber"),
                "nationality": driver.get("nationality"),
                "date_of_birth": driver.get("dateOfBirth"),
            },
        })
    return entities


def race_result_to_driver_entities(payload: dict, sport: str) -> list[dict]:
    """Every driver entity from the same race-results response used to
    build the event item above -- there's no separate driver-roster
    endpoint this project needs, same "the results fetch IS the entity
    source" pattern library/normalize/pga.py uses for golfers."""
    return _driver_entities(payload, sport, "Results")


def sprint_result_to_driver_entities(payload: dict, sport: str) -> list[dict]:
    """Same drivers already normalized from this weekend's main race in
    the overwhelmingly common case -- provided mainly for the rare edge
    case of a reserve driver appearing in the Sprint whose entity hasn't
    been captured yet for whatever reason."""
    return _driver_entities(payload, sport, "SprintResults")


def _constructor_entities(payload: dict, sport: str, results_key: str) -> list[dict]:
    races = payload.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    if not races:
        return []
    entities = []
    seen: set[str] = set()
    for result in races[0].get(results_key, []):
        constructor = result.get("Constructor", {})
        constructor_id = constructor.get("constructorId")
        if not constructor_id or constructor_id in seen:
            continue
        seen.add(constructor_id)
        entities.append({
            "entity_key": entity_key(sport, constructor_id, "team"),
            "entity_id": constructor_id,
            "sport": sport,
            "entity_type": "team",
            "name": constructor.get("name", ""),
            "metadata": {
                "nationality": constructor.get("nationality"),
            },
        })
    return entities


def race_result_to_constructor_entities(payload: dict, sport: str) -> list[dict]:
    """Constructor ("team") entities, type-aware keyed (entity_key(sport,
    id, "team")) so a low-digit constructor id (e.g. "1", Red Bull's own
    Ergast id in some eras) can never collide with a driver's own numeric
    id -- the same collision-avoidance fix already applied for NBA and
    PGA's team match play (see design/DATA_SCHEMA.md)."""
    return _constructor_entities(payload, sport, "Results")


def sprint_result_to_constructor_entities(payload: dict, sport: str) -> list[dict]:
    """Same constructors already normalized from this weekend's main
    race in the overwhelmingly common case -- see sprint_result_to_
    driver_entities' own docstring for why this exists anyway."""
    return _constructor_entities(payload, sport, "SprintResults")


def _parse_lap_time_seconds(value: str | None, context: str = "") -> float | None:
    """Ergast/Jolpica lap-time strings are USUALLY "M:SS.sss" (e.g.
    "1:29.374") -- minutes, then seconds+milliseconds. But a lap under a
    minute has no minutes prefix at all, just bare "SS.sss" (e.g.
    "54.963") -- a real, genuinely valid value this function's first
    version treated as a parse FAILURE (silently discarded as "missing"
    instead of the real time it was), confirmed live 2026-08-31 from a
    real backfill run's own logs -- dozens of real sub-minute qualifying
    times, all logged as "unparseable" and dropped. The colon-only
    assumption came from this module's own original spot-check, which
    only ever happened to sample >=1-minute times. Both shapes are
    handled directly on whether a colon is present, rather than assuming
    one and treating the other as a failure.

    None/empty/malformed still returns None -- a driver eliminated in Q1
    genuinely has no Q2/Q3 KEY AT ALL (confirmed live, not just a null
    value), and that needs to stay a silent, expected gap, not a parse
    failure. `context` (e.g. "season 2014 round 7") is included in the
    warning for any value that's STILL unparseable after handling both
    shapes -- the original warning carried no season/round at all, which
    is exactly why this bug's own real occurrences couldn't be traced
    back to a specific round from the log alone."""
    if not value:
        return None
    try:
        if ":" in value:
            minutes_str, seconds_str = value.split(":")
            return int(minutes_str) * 60 + float(seconds_str)
        return float(value)
    except (ValueError, AttributeError):
        logger.warning("Unparseable F1 lap time %r%s -- treating as missing", value, f" ({context})" if context else "")
        return None


def qualifying_payload_to_results(payload: dict) -> dict[str, dict]:
    """{driver_id: {"position", "q1_seconds", "q2_seconds", "q3_seconds",
    "best_seconds", "gap_to_pole_seconds"}} from JolpicaClient.
    get_qualifying(season, round)'s raw response. No `sport` parameter
    (unlike every entity/event builder above) -- this returns a plain
    driver_id-keyed dict to be merged onto an already-built event item
    by merge_qualifying_into_event below, not a standalone entity/event
    shape of its own.

    best_seconds is whichever of Q3/Q2/Q1 is this driver's OWN deepest
    segment reached -- the literal lap time that actually determined
    their qualifying position, not necessarily their single fastest lap
    across all three segments. gap_to_pole_seconds is that value minus
    the field's own fastest best_seconds (the pole-sitter's own
    qualifying-deciding lap), computed in a second pass once every
    driver's best_seconds is known -- None for the pole-sitter's own row
    would be wrong (their gap to themselves is 0, not "no gap
    computed"), so this is only left None when best_seconds itself is
    None (no time set at all)."""
    races = payload.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    if not races:
        return {}
    race = races[0]
    context = f"season {race.get('season')} round {race.get('round')}"

    parsed: dict[str, dict] = {}
    for result in race.get("QualifyingResults", []):
        driver_id = (result.get("Driver") or {}).get("driverId")
        if not driver_id:
            continue
        q1 = _parse_lap_time_seconds(result.get("Q1"), context)
        q2 = _parse_lap_time_seconds(result.get("Q2"), context)
        q3 = _parse_lap_time_seconds(result.get("Q3"), context)
        best = q3 if q3 is not None else (q2 if q2 is not None else q1)
        parsed[driver_id] = {
            "position": _int_or_none(result.get("position")),
            "q1_seconds": q1, "q2_seconds": q2, "q3_seconds": q3,
            "best_seconds": best, "gap_to_pole_seconds": None,
        }

    pole_time = min((r["best_seconds"] for r in parsed.values() if r["best_seconds"] is not None), default=None)
    if pole_time is not None:
        for r in parsed.values():
            if r["best_seconds"] is not None:
                r["gap_to_pole_seconds"] = round(r["best_seconds"] - pole_time, 3)

    return parsed


def merge_qualifying_into_event(event_item: dict, qualifying_payload: dict | None) -> dict:
    """Merges qualifying_payload_to_results' own output onto
    event_item["participants"][*]["result"]["qualifying"], matched by
    entity_id -- mutates and returns the SAME event_item dict. The one
    "combine two separate raw fetches into one event" step this module
    needs that PGA never did (PGA's single leaderboard fetch already
    carries everything).

    qualifying_payload is the RAW Jolpica qualifying response (or None
    if it hasn't been ingested yet) -- this function owns calling
    qualifying_payload_to_results itself so every caller passes the same
    raw shape regardless of whether qualifying data actually exists yet.

    A participant with no matching qualifying row (didn't set a time,
    or qualifying genuinely hasn't been ingested yet) gets
    `"qualifying": None`, not a missing key -- every participant carries
    the same column set either way, same discipline library/features/
    f1.py's own build_driver_event_features relies on for a consistent
    Parquet schema."""
    qualifying_by_driver = qualifying_payload_to_results(qualifying_payload) if qualifying_payload else {}
    for participant in event_item.get("participants", []):
        participant["result"]["qualifying"] = qualifying_by_driver.get(participant["entity_id"])
    return event_item


def schedule_payload_to_scheduled_events(payload: dict, sport: str) -> list[dict]:
    """One stub event per race on the season's own calendar
    (JolpicaClient.get_races(season)'s raw response -- the SAME schedule
    call aws-lambdas/f1/ingest/handler.py already makes every run to find
    which round is in its own trailing results window), each with
    status="scheduled" and an empty participants list -- circuit_id/
    event_date/venue/race_name are all real and known well ahead of the
    race itself; only the per-driver result is genuinely unknown yet.

    Exists because Jolpica has no separate "what's upcoming" scoreboard
    endpoint the way ESPN gives every other sport's own schedule-sync
    Lambda (library/http/f1.py's own docstring) -- season simulation
    (aws-lambdas/f1/predict/season_projection.py) needs the REMAINING
    calendar's own circuit_id/event_date to build a projected feature row
    for each of those races, and without a stored "scheduled" event at
    all, storage.get_all_events(sport, status="scheduled") would return
    nothing, ever -- the season projection would always fall through to
    "nothing left to simulate," even mid-season.

    Unlike ingest's own per-round results/qualifying/sprint fetches
    (which only ever look at a short trailing window around today, see
    ingest/handler.py's TRAILING_WINDOW_DAYS), this walks the WHOLE
    season's calendar at once, matching how f1-ingest already fetches it
    for its own window-filtering -- one extra S3 write, zero extra
    Jolpica requests.

    Each returned item's own event_id/event_key exactly matches what
    race_result_to_event_item will later produce for the SAME round once
    real results exist -- normalize's own _process_schedule (aws-lambdas/
    f1/normalize/handler.py) relies on this to upsert-refresh a stub
    without ever overwriting an already-completed race's real result (it
    checks the existing stored event's own status first, not this
    function's business)."""
    races = payload.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    return [_event_item_from_race(race, sport, results_key="Results", event_type="field") for race in races]
