"""
PGA team/individual match-play normalizers -- Ryder Cup, Presidents Cup,
and WGC-Dell Technologies Match Play. A genuinely different shape from
library/normalize/pga.py's Medal/Teamstroke normalizers, not a variant of
them: `event["competitions"]` is nested (`[[{...}], [{...}], ...]`, one
outer entry per session/day) instead of every flat-stroke-play event's
single-element list, and results are match-play outcomes (won/halved/
margin-in-holes) rather than stroke scores -- see is_flat_stroke_play's
own docstring in pga.py for why these can't share normalizers.

All field names below verified against real ESPN leaderboard responses,
2026-08-26: a real Presidents Cup (event 401465497, USA d. INTL 17.5-12.5,
every session type -- foursomes/fourball/singles -- and a real halved
match), a real WGC-Dell Technologies Match Play (event 401353293, group
play through Championship), and two real editions of the made-for-TV
exhibition "The Match" (events 401380903/401430881) used to derive the
exhibition-exclusion shape below.

Two distinct real-world outcomes live in one tournament's leaderboard
response here, each its own event_type:
  - "match_play": one row per INDIVIDUAL match (a session's own foursome/
    fourball/singles pairing, or one WGC bracket match) -- see
    leaderboard_event_to_match_event_items.
  - "cup": one row for the OVERALL team result (Ryder Cup/Presidents Cup
    only -- WGC Match Play has no team layer, so no such row exists for
    it) -- see leaderboard_event_to_cup_event_item.
"""
import logging

from library.normalize import pga
from library.schema.keys import entity_key, event_key

logger = logging.getLogger(__name__)


def _flatten_sessions(competitions: list) -> list[dict]:
    """competitions is the nested [[...], [...], ...] shape every
    Match-scored event uses (one outer entry per session/day, or a
    single-entry outer list for a one-session exhibition) -- flattens to
    one flat list of individual competition dicts (one per match, plus
    the Cup-level summary entry when one exists)."""
    return [entry for session in (competitions or []) for entry in session]


def _is_cup_summary(entry: dict) -> bool:
    """The one entry (Ryder Cup/Presidents Cup only) carrying the
    tournament-wide team score -- `description: "tournament"` /
    `scoringSystem.name: "Cup"`, confirmed live 2026-08-26. Distinct from
    the event's own top-level `tournament.scoringSystem` ("Match"), which
    is a different field at a different nesting level."""
    return (entry.get("scoringSystem") or {}).get("name") == "Cup"


def _cup_summary_entry(flat: list[dict]) -> dict | None:
    return next((e for e in flat if _is_cup_summary(e)), None)


def _match_entries(flat: list[dict]) -> list[dict]:
    return [e for e in flat if not _is_cup_summary(e)]


def _uses_team_layer(flat: list[dict]) -> bool:
    """Whether this event's own individual matches are contested by
    national teams (`competitor["team"]`, Ryder Cup/Presidents Cup/The
    Match) rather than individual golfers head-to-head (`competitor
    ["athlete"]` with no `"team"` key at all, WGC Match Play -- confirmed
    live, every WGC competitor checked)."""
    matches = _match_entries(flat)
    if not matches:
        return False
    first_competitors = matches[0].get("competitors") or []
    return bool(first_competitors) and "team" in first_competitors[0]


def is_match_scoring(event: dict) -> bool:
    return (event.get("tournament") or {}).get("scoringSystem", {}).get("name") == "Match"


def is_team_match_play(event: dict) -> bool:
    """Ryder Cup / Presidents Cup -- Match-scored, with a real Cup-level
    summary entry. The one shape-based signal that reliably distinguishes
    these from The Match (see is_exhibition below), since both share the
    identical team+roster competitor shape otherwise."""
    if not is_match_scoring(event):
        return False
    flat = _flatten_sessions(event.get("competitions") or [])
    return _cup_summary_entry(flat) is not None


def is_individual_match_play(event: dict) -> bool:
    """WGC-Dell Technologies Match Play (discontinued after 2023, but
    still real history within this project's 2017-2026 backfill window)
    -- Match-scored, individual golfer vs. golfer, no team layer, no Cup
    summary."""
    if not is_match_scoring(event):
        return False
    flat = _flatten_sessions(event.get("competitions") or [])
    if _cup_summary_entry(flat) is not None:
        return False
    return not _uses_team_layer(flat)


def is_exhibition(event: dict) -> bool:
    """The Match -- Match-scored, team-based (same team+roster shape as
    Ryder Cup/Presidents Cup), but with no Cup-level summary entry (it's
    a single one-off session, not a multi-day team competition with an
    aggregate score). Structural, not name-based -- confirmed live on two
    real editions, 2026-08-26, including one (401430881) whose
    "athletes" aren't reliably PGA Tour golfers at all (Tom Brady/Aaron
    Rodgers vs. Patrick Mahomes/Josh Allen), which is exactly why this
    project excludes it permanently rather than normalizing it: no
    competitive/predictive value, and a real risk of polluting the
    golfer entities table with non-golfers. A future exhibition sharing
    this same "team layer, no Cup summary" shape is caught here too; one
    that doesn't would need its own new check -- fail closed, not a
    guaranteed-complete exclusion list."""
    if not is_match_scoring(event):
        return False
    flat = _flatten_sessions(event.get("competitions") or [])
    return _cup_summary_entry(flat) is None and _uses_team_layer(flat)


def is_supported_match_play(event: dict) -> bool:
    """Whether this project's normalizers below can handle this event --
    team match play (Ryder Cup/Presidents Cup) or individual match play
    (WGC Match Play). False for exhibitions (is_exhibition) and for
    anything that isn't Match-scored at all (Medal/Teamstroke, handled by
    pga.py instead, or an unrecognized future scoring system)."""
    return is_team_match_play(event) or is_individual_match_play(event)


def _competitor_golfers(competitor: dict) -> list[tuple[str, dict]]:
    """(golfer_id, athlete_dict) pairs for one match competitor -- a
    foursomes/fourball pairing's `roster` (2 golfers) or a singles/WGC
    competitor's own `athlete` (1 golfer)."""
    roster = competitor.get("roster")
    if roster:
        return [
            (str(r["athlete"]["id"]), r["athlete"])
            for r in roster if r.get("athlete", {}).get("id") is not None
        ]
    athlete = competitor.get("athlete")
    if athlete and athlete.get("id") is not None:
        return [(str(athlete["id"]), athlete)]
    return []


def _match_result(competitor: dict) -> dict:
    """won/halved/margin come from `score` -- confirmed live across every
    real session type: a winning side has `displayValue` "N & M" (won
    early, e.g. "6 & 5") or "N Up" (won on the 18th), `winner: true`,
    `value` = N; the losing side has an EMPTY displayValue, `winner:
    false`, `value: 0.0`; a halved (tied) match has `displayValue:
    "Halved"`, `draw: true` on BOTH sides, `value: 0.0`. A tied Ryder Cup/
    Presidents Cup itself (both sides finishing 14-14 or similar) hasn't
    been observed live in this project's 2017-2026 window, but is handled
    the same defensive way (won=False, halved=True) if it ever occurs."""
    score = competitor.get("score") or {}
    status = competitor.get("status", {})
    won = bool(score.get("winner", False))
    return {
        "status": pga.map_status(status.get("type", {})),
        "won": won,
        "halved": bool(score.get("draw", False)),
        "margin_display": score.get("displayValue") or "",
        "margin_holes": score.get("value") if won else None,
    }


def _match_participant(competitor: dict) -> dict:
    golfer_ids = [gid for gid, _ in _competitor_golfers(competitor)]
    team = competitor.get("team")
    # Team match play: entity_id is the national TEAM (USA/INTL/EUROPE --
    # see leaderboard_event_to_matchplay_team_entities). Individual match
    # play (WGC, no team layer): the golfer's own id serves double duty
    # as both "entity_id" and its own single-element golfer_entity_ids,
    # so downstream feature code can treat both event shapes uniformly
    # without branching on event_type again.
    entity_id = str(team["id"]) if team and team.get("id") is not None else (golfer_ids[0] if golfer_ids else None)
    return {
        "entity_id": entity_id,
        "role": competitor.get("homeAway"),
        "golfer_entity_ids": golfer_ids,
        "result": _match_result(competitor),
    }


def leaderboard_event_to_match_event_items(event: dict, sport: str) -> list[dict]:
    """One row per individual match (a session's own foursome/fourball/
    singles pairing, or one WGC bracket match) -- `event` is
    get_leaderboard(event_id)["events"][0]. event_id is synthesized as
    f"{tournament_event_id}-match-{match_id}" (match_id is ESPN's own
    inner competition id, e.g. "10951" -- confirmed unique per real
    tournament) since one leaderboard response covers many matches, not
    the one-event-one-id assumption every other normalizer in this
    project makes.

    Raises ValueError up front for an unsupported event (Medal/
    Teamstroke -- routed to pga.py instead; The Match -- excluded
    permanently, see is_exhibition), same defense-in-depth convention
    every PGA normalizer guard uses. Real callers must check
    is_supported_match_play(event) first."""
    if not is_supported_match_play(event):
        raise ValueError(
            f"Event {event.get('id')!r} ({(event.get('tournament') or {}).get('displayName')!r}) is not "
            f"supported team/individual match play -- callers must check is_supported_match_play(event) first.",
        )
    tournament_event_id = event["id"]
    tournament = event.get("tournament") or {}
    course = pga.host_course(event.get("courses", []))
    address = course.get("address") or {}
    flat = _flatten_sessions(event.get("competitions") or [])

    items = []
    for entry in _match_entries(flat):
        match_id = entry["id"]
        match_event_id = f"{tournament_event_id}-match-{match_id}"
        participants = [_match_participant(c) for c in entry.get("competitors", [])]
        items.append({
            "event_key": event_key(sport, match_event_id),
            "event_id": match_event_id,
            "sport": sport,
            "event_type": "match_play",
            # Each match carries its own `date` (confirmed live -- a
            # Sunday singles match's date is genuinely later than a
            # Thursday foursomes match's), not the tournament's own
            # top-level start date -- correct chronological placement
            # matters for feature-engineering's history walk (a Sunday
            # match must not see Thursday's own match result as "future"
            # relative to the tournament, and vice versa).
            "event_date": (entry.get("date") or event.get("date") or "")[:10],
            "status": pga.event_status(entry.get("status", {})),
            "parent_event_id": tournament_event_id,
            "tournament_name": tournament.get("displayName"),
            "session_name": entry.get("description"),
            "match_format": (entry.get("type") or {}).get("text"),
            "participants": participants,
            "season": event.get("season", {}).get("year"),
            "season_type": event.get("seasonType", {}).get("id"),
            "venue_name": course.get("name"),
            "venue_city": address.get("city"),
            "venue_state": address.get("state"),
            "course_id": course.get("id"),
        })
    return items


def leaderboard_event_to_cup_event_item(event: dict, sport: str) -> dict | None:
    """The overall team result -- Ryder Cup/Presidents Cup only. Returns
    None for WGC Match Play (is_individual_match_play), which has no
    team layer and no Cup-level summary entry to build this from -- a
    plain None, not a raised error, since "this tournament has no team
    result" is a real, expected outcome for that format, not a malformed
    payload. Raises ValueError for anything is_supported_match_play
    itself would reject (Medal/Teamstroke/The Match/unrecognized), same
    as leaderboard_event_to_match_event_items."""
    if not is_supported_match_play(event):
        raise ValueError(
            f"Event {event.get('id')!r} ({(event.get('tournament') or {}).get('displayName')!r}) is not "
            f"supported team/individual match play -- callers must check is_supported_match_play(event) first.",
        )
    if not is_team_match_play(event):
        return None

    flat = _flatten_sessions(event.get("competitions") or [])
    cup = _cup_summary_entry(flat)
    tournament_event_id = event["id"]
    tournament = event.get("tournament") or {}
    course = pga.host_course(event.get("courses", []))
    address = course.get("address") or {}

    raw_participants = []
    for competitor in cup.get("competitors", []):
        team = competitor.get("team") or {}
        score = competitor.get("score") or {}
        team_id = team.get("id")
        if team_id is None:
            continue
        raw_participants.append({
            "entity_id": str(team_id), "role": competitor.get("homeAway"), "points": score.get("value"),
            "winner": bool(score.get("winner", False)),
        })

    # A tied Cup (both sides finish level, neither `winner: true`) hasn't
    # been observed live in this project's 2017-2026 backfill window, but
    # a real historical Presidents Cup HAS ended tied before (2003) -- so
    # this is derived directly from point equality (not just trusting
    # `winner` being false on both sides, which "no data yet" would also
    # produce) rather than assumed away. Kept alongside `won`, same
    # halved/won split _match_result already uses for individual matches
    # -- library/features/pga.py's build_cup_event_features excludes a
    # halved Cup from the win-probability label, same "filter at train
    # time" treatment a halved individual match gets.
    points = [p["points"] for p in raw_participants if p["points"] is not None]
    halved = len(points) == 2 and points[0] == points[1]
    participants = [
        {"entity_id": p["entity_id"], "role": p["role"], "result": {"points": p["points"], "won": p["winner"], "halved": halved}}
        for p in raw_participants
    ]

    return {
        "event_key": event_key(sport, tournament_event_id),
        "event_id": tournament_event_id,
        "sport": sport,
        "event_type": "cup",
        "event_date": (event.get("date") or "")[:10],
        "status": pga.event_status(event.get("status", {})),
        "tournament_name": tournament.get("displayName"),
        "participants": participants,
        "season": event.get("season", {}).get("year"),
        "season_type": event.get("seasonType", {}).get("id"),
        "venue_name": course.get("name"),
        "venue_city": address.get("city"),
        "venue_state": address.get("state"),
        "course_id": course.get("id"),
    }


def leaderboard_event_to_matchplay_team_entities(event: dict, sport: str) -> list[dict]:
    """National team entities (USA/INTL/EUROPE, ...) from the Cup-level
    summary's own competitors -- entity_type "team", type-aware keyed
    (entity_key) so a low-digit team id (e.g. "1") can never collide with
    a golfer's own numeric athlete id, same fix this project already
    applied for NBA's team/player id collision. Empty list for
    individual match play (WGC -- no team layer at all)."""
    if not is_team_match_play(event):
        return []
    flat = _flatten_sessions(event.get("competitions") or [])
    cup = _cup_summary_entry(flat)
    entities = []
    for competitor in (cup.get("competitors", []) if cup else []):
        team = competitor.get("team") or {}
        team_id = team.get("id")
        if team_id is None:
            continue
        entities.append({
            "entity_key": entity_key(sport, team_id, "team"),
            "entity_id": str(team_id),
            "sport": sport,
            "entity_type": "team",
            "name": team.get("displayName", ""),
            "metadata": {"abbreviation": team.get("abbreviation")},
        })
    return entities


def leaderboard_event_to_matchplay_player_entities(event: dict, sport: str) -> list[dict]:
    """Every golfer entity appearing in any individual match -- covers
    both team match play (roster-nested athletes) and individual match
    play (WGC's direct athlete). Deduplicated across matches (the same
    golfer plays multiple sessions in a real Ryder Cup/Presidents Cup)."""
    flat = _flatten_sessions(event.get("competitions") or [])
    entities: dict[str, dict] = {}
    for entry in _match_entries(flat):
        for competitor in entry.get("competitors", []):
            for golfer_id, athlete in _competitor_golfers(competitor):
                if golfer_id in entities:
                    continue
                entities[golfer_id] = {
                    "entity_key": entity_key(sport, golfer_id, "player"),
                    "entity_id": golfer_id,
                    "sport": sport,
                    "entity_type": "player",
                    "name": athlete.get("displayName", ""),
                    "metadata": {
                        "country": (athlete.get("flag") or {}).get("alt"),
                        "amateur": bool(athlete.get("amateur", False)),
                    },
                }
    return list(entities.values())
