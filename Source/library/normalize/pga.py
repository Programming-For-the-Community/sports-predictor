"""
PGA Tour (field-event) normalizers -- ESPN-leaderboard-response-to-
project-schema, the field-event counterpart to library/normalize/espn.py's
head-to-head normalizers. A genuinely different shape, not a variant of
the shared ones: a golfer's own entity IS the participant (no team, no
role, no separate box-score fetch -- see design/DATA_SCHEMA.md's field-
event participants section), so this lives in its own module rather than
adding event_type branches to espn.py's functions.

All field names below verified against a real ESPN golf leaderboard
response before being written (site.web.api.espn.com/apis/site/v2/sports/
golf/leaderboard?event={id}), per this project's own "verify raw fields
before feature code" rule -- see project-pga-onboarding memory.
"""
import logging

from library.parsing import parse_number
from library.schema.keys import entity_key, event_key

logger = logging.getLogger(__name__)

# Confirmed live so far: STATUS_FINISH (a no-cut FedEx Cup event),
# STATUS_CUT (a real missed-cut event), STATUS_SCHEDULED (a real
# not-yet-started tournament -- every competitor pre-listed, no round
# played yet). Withdrawal/disqualification not yet seen in a real
# response -- _map_status's fallback below handles those (and anything
# else ESPN adds) without guessing an exact string, logging so a real
# case can be added here once actually observed.
_STATUS_MAP = {
    "STATUS_FINISH": "finished",
    "STATUS_CUT": "cut",
    "STATUS_SCHEDULED": "scheduled",
}


def _map_status(status_type: dict) -> str:
    name = status_type.get("name", "")
    if name in _STATUS_MAP:
        return _STATUS_MAP[name]
    logger.warning("Unmapped PGA status.type.name %r -- falling back to a generic mapping", name)
    return name.removeprefix("STATUS_").lower() or "unknown"


def _parse_finish_position(position: dict | None) -> tuple[int | None, bool]:
    """position.displayName: "T26" -> (26, True), "1" -> (1, False),
    "-" (no finish -- cut/withdrawn/hasn't played) -> (None, False)."""
    if not position:
        return None, False
    display = (position.get("displayName") or "").strip()
    is_tie = bool(position.get("isTie", False))
    digits = display.lstrip("T")
    if not digits.isdigit():
        return None, False
    return int(digits), is_tie


def _parse_score(score: dict) -> tuple[int | float | None, float | None]:
    """(score_to_par, total_strokes) from one competitor's `score` object.

    displayValue is relative-to-par, already signed ("-4", "+2") except
    for even par, which ESPN represents as the literal string "E" rather
    than "0", and "-" for a golfer with no score yet (confirmed live on a
    real not-yet-started tournament's leaderboard, 2026-08-24 -- every
    competitor is pre-listed with score {"value": 0.0, "displayValue":
    "-"} before their round starts, so a literal 0 strokes here is a
    sentinel for "hasn't played," not a real total). parse_number alone
    would leave "E"/"-" as unparsed strings and 0.0 as a bogus stroke
    count, so both are special-cased together here rather than treating
    total_strokes as a plain passthrough of score.value."""
    display_value = score.get("displayValue")
    if display_value == "-":
        return None, None
    if display_value == "E":
        return 0, score.get("value")
    return parse_number(display_value), score.get("value")


def _parse_rounds(linescores: list[dict]) -> list[dict]:
    """One entry per round this golfer actually played, from
    competitor["linescores"] -- confirmed live, 2026-08-25, always
    present (100% of competitors checked across two real tournaments) at
    the ROUND grain (a `period`/`value`/`displayValue` triple per round),
    though the further-nested HOLE-level breakdown within each round is
    not consistently populated and isn't parsed here.

    A cut golfer's own linescores naturally has only 2 entries, not 4 --
    confirmed live directly on a real missed-cut player -- so a golfer
    who didn't play rounds 3-4 simply contributes no entries for them at
    all here, with no conditional cut-logic needed in this function or
    anywhere downstream that consumes it (feature engineering, training).

    Each round's own score is parsed through the exact same _parse_score
    used for the tournament-level total -- an individual round can show
    "E" for even par the same way an overall score can."""
    rounds = []
    for linescore in linescores:
        period = linescore.get("period")
        if period is None:
            continue
        score_to_par, total_strokes = _parse_score(
            {"displayValue": linescore.get("displayValue"), "value": linescore.get("value")},
        )
        rounds.append({"round": period, "score_to_par": score_to_par, "total_strokes": total_strokes})
    return rounds


def _competitor_to_participant(competitor: dict) -> dict:
    athlete = competitor.get("athlete", {})
    status = competitor.get("status", {})
    score_to_par, total_strokes = _parse_score(competitor.get("score") or {})
    finish_position, is_tie = _parse_finish_position(status.get("position"))
    return {
        "entity_id": str(athlete["id"]),
        "result": {
            "finish_position": finish_position,
            "is_tie": is_tie,
            "status": _map_status(status.get("type", {})),
            "score_to_par": score_to_par,
            "total_strokes": total_strokes,
            "earnings": competitor.get("earnings", 0.0),
            # rounds -- added 2026-08-25 specifically for per-round score
            # projection features/models (library/features/pga.py). See
            # _parse_rounds' own docstring for the confirmed-live shape.
            "rounds": _parse_rounds(competitor.get("linescores") or []),
        },
    }


def _event_status(status: dict) -> str:
    return "completed" if status.get("type", {}).get("completed") else "scheduled"


def _host_course(courses: list[dict]) -> dict:
    """The `host: true` course entry -- see this project's own note on
    why golf has no single top-level `venue` object (design/
    DATA_SCHEMA.md). Falls back to the first course if none is flagged
    host (not observed live, but courses is never empty for a real
    tournament)."""
    return next((c for c in courses if c.get("host")), courses[0] if courses else {})


def is_medal_scoring(event: dict) -> bool:
    """Whether this tournament uses standard individual stroke-play
    scoring (ESPN's tournament.scoringSystem.name == "Medal") -- the only
    shape this project's schema/normalizers are built for. PGA TOUR's
    real calendar also carries genuinely different formats this project
    does NOT support: team match play (Ryder Cup, Presidents Cup,
    WGC-Dell Technologies Match Play -- scoringSystem "Match"), team
    stroke play (Zurich Classic of New Orleans -- "Teamstroke"), and
    made-for-TV exhibitions (The Match). All confirmed live, 2026-08-25,
    to use a genuinely different `event["competitions"]` shape (a list of
    per-match/session dicts wrapped in an EXTRA list layer -- `[[{...}],
    [{...}], ...]` instead of every Medal event's flat `[{...}]`) and/or
    a `"team"` key in place of `"athlete"` on each competitor -- either
    one crashes `leaderboard_event_to_event_item` (an AttributeError from
    the extra list layer, or a KeyError from the missing `athlete` key)
    if fed through unchanged, reproduced directly against real Ryder Cup/
    WGC Match Play/Zurich Classic/The Match responses. Callers (ingest,
    schedule-sync, normalize, backfill) must check this BEFORE calling
    either normalizer function below.

    Missing `tournament`/`scoringSystem` entirely (e.g. a future calendar
    entry ESPN hasn't fully populated yet -- confirmed live on a
    not-yet-configured Presidents Cup entry) is treated as NOT Medal --
    fail closed rather than assume a shape that might not hold."""
    return (event.get("tournament") or {}).get("scoringSystem", {}).get("name") == "Medal"


def leaderboard_event_to_event_item(event: dict, sport: str) -> dict:
    """One PGA tournament -> one events-table item. `event` is
    get_leaderboard(event_id)["events"][0].

    Raises ValueError up front for a non-Medal-scoring event (Ryder Cup/
    Presidents Cup/WGC Match Play/Zurich Classic/etc. -- see
    is_medal_scoring's own docstring) rather than letting the mismatched
    shape crash confusingly further down (an AttributeError or KeyError,
    depending on which one) -- a defense-in-depth guard, not the primary
    one: every real caller (ingest, schedule-sync, normalize, backfill)
    should already check is_medal_scoring(event) BEFORE calling this at
    all, both to avoid the wasted exception and to log a clearer
    "skipping, not stroke play" message than a bare ValueError gives."""
    if not is_medal_scoring(event):
        raise ValueError(
            f"Event {event.get('id')!r} ({(event.get('tournament') or {}).get('displayName')!r}) is not "
            f"Medal (stroke-play) scoring -- this normalizer doesn't support team/match-play events. "
            f"Callers must check is_medal_scoring(event) first.",
        )
    competition = event["competitions"][0]
    participants = [_competitor_to_participant(c) for c in competition.get("competitors", [])]

    event_id = event["id"]
    host_course = _host_course(event.get("courses", []))
    address = host_course.get("address") or {}
    # No season.type on the leaderboard endpoint's `season` (unlike the
    # scoreboard endpoint's) -- confirmed live, it's a bare {"year": ...}
    # here. season_type instead comes from the sibling top-level
    # `seasonType` field. `week` is always an empty {} for golf (no
    # week-number concept, unlike NFL) -- included as None for schema
    # consistency with the head-to-head sports, not a gap.
    tournament = event.get("tournament") or {}
    return {
        "event_key": event_key(sport, event_id),
        "event_id": event_id,
        "sport": sport,
        "event_type": "field",
        "event_date": event["date"][:10],
        "status": _event_status(event.get("status", {})),
        "participants": participants,
        "season": event.get("season", {}).get("year"),
        "season_type": event.get("seasonType", {}).get("id"),
        "week": None,
        "venue_indoor": None,
        "venue_name": host_course.get("name"),
        "venue_city": address.get("city"),
        "venue_state": address.get("state"),
        # course_id -- ESPN's own numeric course id (e.g. "65" for
        # Bellerive Country Club), distinct from venue_name because a
        # sponsor/course-name change over the years (real, if
        # infrequent) would otherwise silently break a course-fit
        # feature keyed by name string. A PGA tournament recurs at the
        # same course_id across seasons far more reliably than by name
        # -- added specifically for library/features/pga.py's own
        # rolling per-course history.
        "course_id": host_course.get("id"),
        # purse/is_major -- field-strength context a ranking model needs
        # (design/DATA_SCHEMA.md and library/features/pga.py), not present
        # on any head-to-head sport's event item. purse comes from the
        # event's own top-level `purse` (a plain integer, USD); is_major
        # from the nested `tournament.major` flag -- both confirmed live
        # on a real leaderboard response, 2026-08-24.
        "purse": event.get("purse"),
        "is_major": bool(tournament.get("major", False)),
        # cut_score/cut_round/cut_count -- for the projected-cut-line
        # model (library/features/pga.py, feature-engineering/pga/
        # build_dataset.py's build_cutline_dataset). All three come
        # straight off the leaderboard's own `tournament` object,
        # confirmed live, 2026-08-25, on both a real-cut event (a 71-
        # player-field Genesis Scottish Open, cutRound=2, cutScore=-2,
        # cutCount=71) and a no-cut FedEx Cup playoff event (BMW
        # Championship, all three genuinely 0 -- treated as "this
        # tournament had no cut," not missing data, so cut-line training
        # filters to cut_count > 0 rather than cut_score.notna()).
        "cut_score": tournament.get("cutScore"),
        "cut_round": tournament.get("cutRound"),
        "cut_count": tournament.get("cutCount"),
    }


def leaderboard_event_to_player_entities(event: dict, sport: str) -> list[dict]:
    """Every competitor's own golfer entity from the same leaderboard
    response used to build the event item above -- there's no separate
    roster endpoint for golf (see library/http/pga.py's own docstring), so
    this is the only source of PGA player entities.

    metadata.country comes from athlete.flag.alt (a 3-letter country
    code, e.g. "USA") rather than birthPlace.countryAbbreviation, which is
    absent for some competitors -- flag is present on every real
    competitor seen so far.

    Raises ValueError up front for a non-Medal-scoring event, same
    defense-in-depth guard as leaderboard_event_to_event_item -- without
    it, a team/match-play event would either crash here too (the same
    extra-list-layer AttributeError) or silently return an empty list
    (Zurich Classic's team-based competitors have no `athlete` key at
    all, so every entry's athlete_id resolves to None and gets skipped),
    neither of which is an honest signal that this wasn't a real
    per-golfer entity list."""
    if not is_medal_scoring(event):
        raise ValueError(
            f"Event {event.get('id')!r} ({(event.get('tournament') or {}).get('displayName')!r}) is not "
            f"Medal (stroke-play) scoring -- this normalizer doesn't support team/match-play events. "
            f"Callers must check is_medal_scoring(event) first.",
        )
    competition = event["competitions"][0]
    entities = []
    for competitor in competition.get("competitors", []):
        athlete = competitor.get("athlete", {})
        athlete_id = athlete.get("id")
        if athlete_id is None:
            continue
        entities.append({
            "entity_key": entity_key(sport, athlete_id, "player"),
            "entity_id": str(athlete_id),
            "sport": sport,
            "entity_type": "player",
            "name": athlete.get("displayName", ""),
            "metadata": {
                "country": (athlete.get("flag") or {}).get("alt"),
                "amateur": bool(competitor.get("amateur", False)),
            },
        })
    return entities
