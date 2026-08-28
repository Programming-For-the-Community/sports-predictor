"""
Projects who's actually going to be IN a remaining (not-yet-played)
event's field, for the FedEx Cup season simulation -- a not-yet-started
tournament's own stored `participants` list is empty/sparse (this is
exactly why this module exists at all), so the simulation needs a
different source for "who's actually playing this week."

Matches a remaining event against the SAME tournament's own prior-season
instance primarily by course_id, NOT tournament_name -- confirmed live,
2026-08-28: `tournament_name` is null on virtually every already-stored
historical PGA event (library/normalize/pga.py's leaderboard_event_to_
event_item only started actually setting it as of a 2026-08-27 fix,
which doesn't retroactively backfill older rows), while course_id is
100% populated across every stored field event checked, past and
scheduled alike. tournament_name is used only as a rare tie-breaker when
a course hosted more than one prior-season event (see match_prior_season_
event) -- the plan this module was originally designed against assumed
the reverse priority; that assumption didn't survive contact with real
data.
"""


def match_prior_season_event(remaining_event: dict, prior_season_events: list[dict]) -> dict | None:
    """The same real-world tournament's own prior-season instance, or
    None if course_id is missing on `remaining_event` or no prior-season
    event shares it at all (a genuinely new tournament on tour, or a
    course_id gap on one side). prior_season_events should already be
    scoped to field events from the season immediately before this one --
    see season_projection.py's own caller."""
    course_id = remaining_event.get("course_id")
    if course_id is None:
        return None
    candidates = [e for e in prior_season_events if e.get("course_id") == course_id]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    # Rare: the same course hosted more than one prior-season field event
    # (e.g. a genuine reschedule). Prefer one whose tournament_name also
    # matches when both sides actually have a real name; otherwise fall
    # back to the most recent candidate rather than guessing further.
    name = remaining_event.get("tournament_name")
    if name is not None:
        named_match = next((c for c in candidates if c.get("tournament_name") == name), None)
        if named_match is not None:
            return named_match
    return max(candidates, key=lambda e: e.get("event_date", ""))


def _confirmed_skip(matched_event: dict | None, entity_id: str) -> bool:
    """True only when there's a real prior-season instance of this event
    AND this golfer has NO participant row in it at all -- a genuine
    skip. A golfer who withdrew still has a participant row (status
    "withdrawn"), so a withdrawal is never mistaken for a skip. No
    matched prior event at all (new tournament, or course_id gap) means
    there's nothing to compare against, so no golfer is ever marked a
    skip from that alone."""
    if matched_event is None:
        return False
    return not any(p.get("entity_id") == entity_id for p in matched_event.get("participants", []))


def project_remaining_field(remaining_event: dict, prior_season_events: list[dict], tracked_roster: list[str]) -> list[str]:
    """Every golfer in `tracked_roster` (this season's own tracked
    roster -- every golfer with >=1 real start already this season; a
    golfer never tracked this season never needs a skip/include decision
    at all, resolving the rookie/no-history question by construction)
    projected to play `remaining_event`'s own field: everyone EXCEPT a
    confirmed skip (see _confirmed_skip) against the matched prior-season
    instance of this same tournament."""
    matched_event = match_prior_season_event(remaining_event, prior_season_events)
    return [entity_id for entity_id in tracked_roster if not _confirmed_skip(matched_event, entity_id)]


def remaining_event_has_cut(remaining_event: dict, prior_season_events: list[dict]) -> bool:
    """Whether `remaining_event` is expected to have a 36-hole cut --
    read off the matched prior-season instance's own stored `cut_count`
    (no new table needed; every completed field event already records
    this). No matched prior event at all means no signal either way --
    defaults to True (a real cut is the norm; only the Playoffs/majors-
    adjacent no-cut events, roughly a handful a year, are the exception,
    so assuming a cut for an unmatched/new tournament is the safer
    default for a cutline projection)."""
    matched_event = match_prior_season_event(remaining_event, prior_season_events)
    if matched_event is None:
        return True
    return (matched_event.get("cut_count") or 0) > 0
