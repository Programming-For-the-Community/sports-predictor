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
# played yet), STATUS_MDF ("Made Cut Did Not Finish" -- a golfer who made
# the cut but withdrew before finishing, e.g. injury mid-round-3;
# confirmed common, not rare -- ~1 in 300 competitor-rows across a live
# sweep of 2017-2025 seasons, 2026-08-26), STATUS_WITHDRAWN (a golfer who
# withdrew before making the cut -- confirmed live during the 2026-08-26/27
# backfill re-run, so ESPN does tag these rather than omitting the
# competitor entirely as the prior sweep had suggested). Disqualification
# not yet seen in a real response. map_status's fallback below handles
# that (and anything else ESPN adds) without guessing an exact string,
# logging so a real case can be added here once actually observed.
_STATUS_MAP = {
    "STATUS_FINISH": "finished",
    "STATUS_CUT": "cut",
    "STATUS_SCHEDULED": "scheduled",
    "STATUS_MDF": "made_cut_did_not_finish",
    "STATUS_WITHDRAWN": "withdrawn",
}


def map_status(status_type: dict) -> str:
    """Public (not module-private) because library/normalize/pga_matchplay.py
    reuses this exact ESPN-status-name mapping for match-play competitor
    statuses -- same STATUS_FINISH/STATUS_SCHEDULED vocabulary, confirmed
    live on real Ryder Cup/Presidents Cup/WGC Match Play responses,
    2026-08-26.

    STATUS_CUT is checked against its own shortDetail before the name-keyed
    table below -- confirmed live, 2026-08-27, on a real TOUR Championship
    withdrawal: ESPN's own status.type.name was "STATUS_CUT" while
    type.shortDetail/description both said "WD"/"Withdrawn". TOUR
    Championship's 30-man field has no 36-hole cut to even miss, so this
    genuinely can't be a real cut -- ESPN reuses STATUS_CUT as a catch-all
    "no longer in real contention" bucket in at least this one case, and
    its own human-readable shortDetail is the more trustworthy signal here.
    Every other status stays governed by `name` exactly as before -- this
    doesn't change a real missed-cut's own mapping, only corrects the one
    ambiguous case ESPN's own name field mislabels."""
    name = status_type.get("name", "")
    if name == "STATUS_CUT" and status_type.get("shortDetail") == "WD":
        return "withdrawn"
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
    total_strokes as a plain passthrough of score.value.

    Any OTHER non-numeric displayValue (confirmed live 2026-08-27, a real
    crash: a withdrawn golfer's score is displayValue "WD", not "-" or a
    signed number) falls back to the same (None, None) "no score" result
    as "-" -- parse_number returns a raw string unparsed rather than
    guessing, and that string reaching rolling_golfer_averages' sum()
    crashes with a str/int TypeError. "WD" isn't special-cased by name
    (a "DQ" or any future ESPN status-string would hit the exact same
    shape) -- fail closed on anything parse_number couldn't turn into a
    real number, same discipline this module's other guards use."""
    display_value = score.get("displayValue")
    if display_value == "-":
        return None, None
    if display_value == "E":
        return 0, score.get("value")
    parsed = parse_number(display_value)
    if isinstance(parsed, str):
        return None, None
    return parsed, score.get("value")


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

    Skips a round with no `displayValue`/`value` key at all -- confirmed
    live 2026-08-27/28 on the real in-progress TOUR Championship: once
    ESPN publishes a golfer's NEXT round's tee time (same day their
    current round wraps up, sometimes the evening before), it adds a
    linescores entry for that round carrying only
    {period, teeTime, hasStream, isPlayoff}, no score fields whatsoever --
    genuinely different from this function's own prior assumption that
    a linescores entry only ever exists for a round already played.
    Without this guard, that stub was parsed as a real round via
    _parse_score's own (None, None) "no score" fallback and appended to
    `rounds` anyway, which corrupts aws-lambdas/pga/predict/
    live_features.py's applicable_rounds() (`played = {r["round"] for r
    in result.get("rounds", [])}` would count the *unplayed* next round
    as played, skipping the model for the actual next round to predict).
    A live check of a real not-yet-started round confirms this is not a
    rare edge case -- it's the norm once next-round tee times exist,
    i.e. for most of the field most of the time.

    Each round's own score is parsed through the exact same _parse_score
    used for the tournament-level total -- an individual round can show
    "E" for even par the same way an overall score can."""
    rounds = []
    for linescore in linescores:
        period = linescore.get("period")
        if period is None:
            continue
        if "displayValue" not in linescore and "value" not in linescore:
            continue  # a future round's tee-time-only stub -- not played yet
        score_to_par, total_strokes = _parse_score(
            {"displayValue": linescore.get("displayValue"), "value": linescore.get("value")},
        )
        rounds.append({"round": period, "score_to_par": score_to_par, "total_strokes": total_strokes})
    return rounds


def _competitor_to_participants(competitor: dict) -> list[dict]:
    """Almost always a single-element list (one Medal-scoring competitor
    is one golfer, keyed by `athlete`) -- except a Teamstroke competitor
    (Zurich Classic of New Orleans, the only non-Medal stroke-play format
    this project supports, confirmed live 2026-08-26), which has no
    `athlete` key at all: it's a 2-golfer pairing keyed by `roster`
    instead ([{"playerId", "athlete": {...}}, ...]), sharing one combined
    score/status/earnings between them. In that case this returns TWO
    participant dicts, one per roster golfer, each carrying the identical
    shared result plus `partner_entity_ids` (the other golfer(s) on the
    same pairing) -- Zurich Classic is otherwise structurally identical
    to a Medal event (same flat `competitions` shape, same STATUS_CUT/
    STATUS_FINISH vocabulary, same score/linescores shape), so a pairing
    naturally slots into the existing top-10/cutline/score/round models
    unchanged: each golfer just shares their partner's result, same as
    they'd share a real prize-money split."""
    status = competitor.get("status", {})
    score_to_par, total_strokes = _parse_score(competitor.get("score") or {})
    finish_position, is_tie = _parse_finish_position(status.get("position"))
    rounds = _parse_rounds(competitor.get("linescores") or [])

    # ESPN's own top-level `score` object only reflects FULLY COMPLETED
    # rounds -- confirmed live, 2026-08-28, on a real in-progress TOUR
    # Championship round 2: linescores[1] already carried real partial
    # strokes (score_to_par -1, 6 holes played) while the top-level
    # `score` object still showed only round 1's own total, well behind
    # status.position (which IS already live/correct -- ESPN computes the
    # real current leaderboard rank from data it doesn't also expose
    # here). A real user-reported bug: placement updated live while the
    # to-par standing and sort order both stayed stuck on round 1. Fixed
    # by deriving score_to_par/total_strokes from summing the already-
    # parsed per-round rows instead -- those DO include the live partial
    # in-progress round -- whenever at least one round has a real value.
    # Only when `rounds` has nothing real at all (a golfer who hasn't
    # teed off yet) does the top-level `score` object's own (correctly
    # "-"/None) reading stand, since an empty sum (0) would misread as
    # "even par" instead of "no score at all".
    round_scores = [r["score_to_par"] for r in rounds if isinstance(r.get("score_to_par"), (int, float))]
    round_strokes = [r["total_strokes"] for r in rounds if isinstance(r.get("total_strokes"), (int, float))]
    if round_scores:
        score_to_par = sum(round_scores)
    if round_strokes:
        total_strokes = sum(round_strokes)

    shared_result = {
        "finish_position": finish_position,
        "is_tie": is_tie,
        "status": map_status(status.get("type", {})),
        "score_to_par": score_to_par,
        "total_strokes": total_strokes,
        "earnings": competitor.get("earnings", 0.0),
        # rounds -- added 2026-08-25 specifically for per-round score
        # projection features/models (library/features/pga.py). See
        # _parse_rounds' own docstring for the confirmed-live shape.
        "rounds": rounds,
    }

    roster = competitor.get("roster")
    if roster:
        golfer_ids = [str(r["athlete"]["id"]) for r in roster if r.get("athlete", {}).get("id") is not None]
        return [
            {
                "entity_id": gid,
                "partner_entity_ids": [other for other in golfer_ids if other != gid],
                "result": shared_result,
            }
            for gid in golfer_ids
        ]

    athlete = competitor.get("athlete", {})
    athlete_id = athlete.get("id")
    if athlete_id is None:
        # A stub athlete object with no "id" at all -- same real gap
        # library/normalize/espn.py's box-score parsing already guards
        # against for a DNP player, confirmed live here 2026-08-26/27 on
        # a real Medal-scoring competitor. leaderboard_event_to_player_
        # entities (below) already skips these silently; this competitor
        # contributes no participant rows either, rather than crashing.
        return []
    return [{"entity_id": str(athlete_id), "result": shared_result}]


def event_status(status: dict) -> str:
    """Public -- reused by library/normalize/pga_matchplay.py's cup/match
    event builders, same completed/scheduled binary either way.

    Checks `type.state == "post"`, not `type.completed`. Confirmed live
    2026-08-27 on a real cached Presidents Cup leaderboard (401465497):
    the TOP-LEVEL tournament status carries both `state: "post"` and
    `completed: true` together, but an INDIVIDUAL match's own status
    object (nested inside `competitions[n][m]["status"]`, the one
    library/normalize/pga_matchplay.py's leaderboard_event_to_match_
    event_items feeds this same function) never carries `completed` at
    all -- only `state`/`name`/`description`. Every match_play event was
    silently written to DynamoDB with status "scheduled" (falsy .get on
    a missing key) forever, so FeatureStorage.get_all_events's default
    status="completed" query found 0 of them -- feature-engineering's
    match/cup dataset build logged "0 match-play... event(s)" and raised
    on the resulting empty match_features.parquet write. `state` is
    present and correct at both nesting levels (confirmed on the same
    real tournament), so one check now covers tournament-level (Medal/
    Teamstroke and Cup-summary) and match-level status alike."""
    return "completed" if status.get("type", {}).get("state") == "post" else "scheduled"


def host_course(courses: list[dict]) -> dict:
    """The `host: true` course entry -- see this project's own note on
    why golf has no single top-level `venue` object (design/
    DATA_SCHEMA.md). Falls back to the first course if none is flagged
    host (not observed live, but courses is never empty for a real
    tournament). Public -- reused by pga_matchplay.py, Ryder Cup/
    Presidents Cup/WGC Match Play responses carry the same `courses`
    shape (confirmed live, 2026-08-26)."""
    return next((c for c in courses if c.get("host")), courses[0] if courses else {})


def is_medal_scoring(event: dict) -> bool:
    """Whether this tournament uses standard individual stroke-play
    scoring (ESPN's tournament.scoringSystem.name == "Medal"). See
    is_flat_stroke_play's own docstring for the sibling "Teamstroke"
    format (Zurich Classic) this normalizer ALSO supports -- most callers
    should check is_flat_stroke_play, not this function directly, unless
    they specifically need to distinguish Medal from Teamstroke.

    Missing `tournament`/`scoringSystem` entirely (e.g. a future calendar
    entry ESPN hasn't fully populated yet -- confirmed live on a
    not-yet-configured Presidents Cup entry) is treated as NOT Medal --
    fail closed rather than assume a shape that might not hold."""
    return (event.get("tournament") or {}).get("scoringSystem", {}).get("name") == "Medal"


def is_team_stroke_play(event: dict) -> bool:
    """Zurich Classic of New Orleans, ESPN's only "Teamstroke" tournament
    -- confirmed live, 2026-08-26, structurally identical to a Medal
    event (same FLAT `competitions` shape, same STATUS_CUT/STATUS_FINISH
    vocabulary, same score/linescores shape), except each competitor is a
    2-golfer pairing (`team.displayName` + `roster`) rather than a single
    `athlete`. See _competitor_to_participants' own docstring for how
    that pairing is expanded into two participant rows."""
    return (event.get("tournament") or {}).get("scoringSystem", {}).get("name") == "Teamstroke"


def is_flat_stroke_play(event: dict) -> bool:
    """Medal or Teamstroke -- the two scoring systems this normalizer
    supports, both sharing the exact same FLAT `event["competitions"]`
    shape (a single-element list) and the same event/participant
    structure below. PGA TOUR's real calendar also carries genuinely
    different formats this normalizer does NOT support: team match play
    (Ryder Cup, Presidents Cup, WGC-Dell Technologies Match Play --
    scoringSystem "Match") and made-for-TV exhibitions (The Match, also
    scored "Match") -- see library/normalize/pga_matchplay.py, which
    handles those instead. All confirmed live, 2026-08-25/26, that a
    "Match"-scored event uses a genuinely different `competitions` shape
    (a list of per-session dicts wrapped in an EXTRA list layer --
    `[[{...}], [{...}], ...]`) that would crash this module's normalizers
    (an AttributeError from the extra list layer) if fed through
    unchanged. Callers (ingest, schedule-sync, normalize, backfill) must
    check this BEFORE calling either normalizer function below, and route
    a "Match"-scored event to pga_matchplay.py instead."""
    return is_medal_scoring(event) or is_team_stroke_play(event)


def _next_tee_time(competitors: list[dict]) -> str | None:
    """Earliest known upcoming tee time across still-in-the-tournament
    competitors (excludes cut/withdrawn/MDF -- they have no round left to
    tee off for), or None if nobody has a known tee time yet.

    Confirmed live 2026-08-27/28 on the real in-progress TOUR
    Championship: each competitor's own `status.teeTime` carries their
    NEXT (or current) round's tee time -- e.g. a golfer who just finished
    today's round already shows tomorrow's published tee time here, often
    the same day it's still being played. This is the one real clock
    signal PGA data has for "when does play next start" -- see
    aws-lambdas/pga/live-scores/live_scores.py's own docstring for why an
    earlier attempt to use the event-level `date` field instead was wrong
    (that field is a static midnight-UTC placeholder, not a real tee
    time, confirmed by the same live check)."""
    tee_times = [
        status["teeTime"]
        for c in competitors
        if (status := c.get("status", {})).get("teeTime")
        and map_status(status.get("type", {})) not in _ELIMINATED_RESULT_STATUSES
    ]
    return min(tee_times) if tee_times else None


# Same vocabulary as aws-lambdas/pga/predict/live_features.py's own
# _ELIMINATED_STATUSES -- duplicated here rather than imported across the
# library/aws-lambdas boundary (library code doesn't import from
# aws-lambdas/). Must stay in sync.
_ELIMINATED_RESULT_STATUSES = {"cut", "withdrawn", "made_cut_did_not_finish"}


def leaderboard_event_to_event_item(event: dict, sport: str) -> dict:
    """One PGA tournament -> one events-table item. `event` is
    get_leaderboard(event_id)["events"][0].

    Raises ValueError up front for a non-flat-stroke-play event (Ryder
    Cup/Presidents Cup/WGC Match Play/The Match -- see
    is_flat_stroke_play's own docstring) rather than letting the
    mismatched shape crash confusingly further down (an AttributeError)
    -- a defense-in-depth guard, not the primary one: every real caller
    (ingest, schedule-sync, normalize, backfill) should already check
    is_flat_stroke_play(event) BEFORE calling this at
    all, both to avoid the wasted exception and to log a clearer
    "skipping, not stroke play" message than a bare ValueError gives."""
    if not is_flat_stroke_play(event):
        raise ValueError(
            f"Event {event.get('id')!r} ({(event.get('tournament') or {}).get('displayName')!r}) is not "
            f"Medal or Teamstroke (stroke-play) scoring -- this normalizer doesn't support team/match-play "
            f"events. Callers must check is_flat_stroke_play(event) first.",
        )
    competition = event["competitions"][0]
    participants = [
        p for c in competition.get("competitors", []) for p in _competitor_to_participants(c)
    ]

    event_id = event["id"]
    course = host_course(event.get("courses", []))
    address = course.get("address") or {}
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
        # next_tee_time -- see _next_tee_time's own docstring. Needed by
        # pga-live-scores' poll-window logic; refreshed every time this
        # function runs (both the daily pga-normalize path and live-
        # scores' own poll), so a day's tee times are typically already
        # known in DynamoDB before live-scores' scheduler ever needs them
        # that day, without a dedicated "discover tee times" call.
        "next_tee_time": _next_tee_time(competition.get("competitors", [])),
        # end_date -- confirmed live on the same leaderboard response
        # (library/http/pga.py's own docstring, 2026-08-24). Needed by
        # pga-live-scores to know which calendar days are tournament
        # days at all.
        "end_date": (event.get("endDate") or "")[:10] or None,
        # tournament_name -- already read by library/serving/pga_reads.py
        # and aws-lambdas/pga/predict/event_prediction.py, but was never
        # set here (only pga_matchplay.py's match_play/cup normalizers
        # set it) -- every field-event tournament_name was silently null.
        "tournament_name": tournament.get("displayName"),
        "status": event_status(event.get("status", {})),
        "participants": participants,
        "season": event.get("season", {}).get("year"),
        "season_type": event.get("seasonType", {}).get("id"),
        "week": None,
        "venue_indoor": None,
        "venue_name": course.get("name"),
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
        "course_id": course.get("id"),
        # par -- this course's own single-round par (e.g. 70), from the
        # SAME host_course entry course_id already comes from
        # (course.shotsToPar, confirmed live 2026-08-28 on a real TOUR
        # Championship leaderboard response). Lets serving-time code
        # convert a score-to-par value (real or model-projected) into an
        # implied stroke count for display, without needing a dedicated
        # strokes-prediction model.
        "par": course.get("shotsToPar"),
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

    A Teamstroke competitor (Zurich Classic) has no `athlete` key at all
    -- its two golfers live in `roster` instead ([{"playerId", "athlete":
    {...}}, ...]), each nested athlete dict carrying the same
    displayName/flag/amateur fields a Medal competitor's own `athlete`
    dict does (confirmed live, 2026-08-26), so both shapes are handled
    below rather than only the flat one.

    Raises ValueError up front for a non-flat-stroke-play event, same
    defense-in-depth guard as leaderboard_event_to_event_item -- without
    it, a team-match-play event would crash here too (the same
    extra-list-layer AttributeError)."""
    if not is_flat_stroke_play(event):
        raise ValueError(
            f"Event {event.get('id')!r} ({(event.get('tournament') or {}).get('displayName')!r}) is not "
            f"Medal or Teamstroke (stroke-play) scoring -- this normalizer doesn't support team/match-play "
            f"events. Callers must check is_flat_stroke_play(event) first.",
        )
    competition = event["competitions"][0]
    entities = []
    for competitor in competition.get("competitors", []):
        roster = competitor.get("roster")
        athletes = [r.get("athlete", {}) for r in roster] if roster else [competitor.get("athlete", {})]
        for athlete in athletes:
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
                    "amateur": bool(athlete.get("amateur", False)),
                },
            })
    return entities
