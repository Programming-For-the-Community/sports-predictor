"""
Projects who's actually going to be in a remaining (not-yet-played)
event's field, for the FedEx Cup season simulation -- a not-yet-started
tournament's own stored `participants` list is empty/sparse, so the
simulation needs a different source for "who's actually playing this
week."

Matches a remaining event against the same tournament's prior-season
instance primarily by course_id, not tournament_name -- tournament_name
is null on most already-stored historical PGA events, while course_id is
populated across every stored field event. tournament_name is used only
as a tie-breaker when a course hosted more than one prior-season event.
"""


def match_prior_season_event(remaining_event: dict, prior_season_events: list[dict]) -> dict | None:
    """The same tournament's prior-season instance, or None if course_id
    is missing on `remaining_event` or no prior-season event shares it.
    prior_season_events should already be scoped to field events from the
    season immediately before this one."""
    course_id = remaining_event.get("course_id")
    if course_id is None:
        return None
    candidates = [e for e in prior_season_events if e.get("course_id") == course_id]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    # Rare: the same course hosted more than one prior-season field event.
    # Prefer one whose tournament_name also matches; otherwise fall back
    # to the most recent candidate.
    name = remaining_event.get("tournament_name")
    if name is not None:
        named_match = next((c for c in candidates if c.get("tournament_name") == name), None)
        if named_match is not None:
            return named_match
    return max(candidates, key=lambda e: e.get("event_date", ""))


def _confirmed_skip(matched_event: dict | None, entity_id: str) -> bool:
    """True only when there's a prior-season instance of this event and
    this golfer has no participant row in it -- a genuine skip. A golfer
    who withdrew still has a participant row (status "withdrawn"), so a
    withdrawal is never mistaken for a skip."""
    if matched_event is None:
        return False
    return not any(p.get("entity_id") == entity_id for p in matched_event.get("participants", []))


def project_remaining_field(remaining_event: dict, prior_season_events: list[dict], tracked_roster: list[str]) -> list[str]:
    """Every golfer in `tracked_roster` (every golfer with >=1 real start
    already this season) projected to play `remaining_event`'s field:
    everyone except a confirmed skip (see _confirmed_skip) against the
    matched prior-season instance of this same tournament."""
    matched_event = match_prior_season_event(remaining_event, prior_season_events)
    return [entity_id for entity_id in tracked_roster if not _confirmed_skip(matched_event, entity_id)]


def remaining_event_has_cut(remaining_event: dict, prior_season_events: list[dict]) -> bool:
    """Whether `remaining_event` is expected to have a 36-hole cut --
    read off the matched prior-season instance's stored `cut_count`. No
    matched prior event defaults to True (a cut is the norm; the
    Playoffs/majors-adjacent no-cut events are the exception)."""
    matched_event = match_prior_season_event(remaining_event, prior_season_events)
    if matched_event is None:
        return True
    return (matched_event.get("cut_count") or 0) > 0
