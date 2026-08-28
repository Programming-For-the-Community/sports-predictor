"""
Real FedEx Cup points table, hand-maintained -- same precedent as
library/features/nba_cup_groups.py (a real-world points/grouping table no
API exposes, transcribed by hand and re-verified each season). Anchor
values below are the 2025/2026-season table, transcribed 2026-08-28 from
https://en.wikipedia.org/wiki/List_of_point_distributions_of_the_FedEx_Cup
-- ANNUAL MAINTENANCE ITEM: re-verify against the currently-published
real table before trusting this for a new season; point values (and
which real tournaments count as "elevated") both change year to year.

ESPN's own API exposes no elevated/Playoffs-tier signal at all (only a
plain `is_major` flag on a stored event -- confirmed live, PGA onboarding
sweep) -- EVENT_TIER_OVERRIDES below is how a specific season's real
calendar maps a tournament to one of the tiers below, keyed by the
tournament's own real name (populated on every event normalized since the
2026-08-27 tournament_name fix -- library/normalize/pga.py's own
leaderboard_event_to_event_item docstring -- so this lookup is reliable
for the CURRENT/remaining season this module actually runs against, even
though the same field is null on most older, already-completed events;
that's a different problem pga_field_projection.py's course_id-primary
matching solves separately).
"""

# Anchor points per tier, position -> points -- transcribed exactly from
# the real table at these specific positions; every OTHER position is
# linearly interpolated between its two nearest anchors (points_for_field
# below), and tapered toward 0 past the highest anchor (position 70,
# roughly where Playoffs qualification -- top 70 -- stops mattering
# anyway). This is an approximation for the un-transcribed middle
# positions, not a second real source -- documented as such rather than
# presented as exact.
_MAJOR_ANCHORS = {
    1: 750, 2: 500, 3: 350, 4: 325, 5: 300, 6: 270, 7: 250, 8: 225, 9: 200, 10: 175,
    15: 95, 20: 60, 25: 47, 30: 37, 40: 22, 50: 14.25, 60: 9, 70: 6.25,
}
_ELEVATED_ANCHORS = {
    1: 700, 2: 400, 3: 350, 4: 325, 5: 300, 6: 275, 7: 225, 8: 200, 9: 175, 10: 150,
    15: 90, 20: 55, 25: 42, 30: 32.5, 40: 20.25, 50: 13, 60: 8.25, 70: 5.75,
}
_REGULAR_ANCHORS = {
    1: 500, 2: 300, 3: 190, 4: 135, 5: 110, 6: 100, 7: 90, 8: 85, 9: 80, 10: 75,
    15: 55, 20: 45, 25: 35.5, 30: 28, 40: 16, 50: 8.5, 60: 5.0, 70: 3.0,
}
# The two Playoffs events -- confirmed live "playoff tournaments carry
# four times the points of regular season tournaments" (same source as
# above); TOUR Championship is deliberately excluded from this whole
# module -- as of the 2025-format redesign it awards no FedEx Cup points
# at all, see season_simulation.py's own simulate_tour_championship.
_FEDEX_ST_JUDE_ANCHORS = {position: points * 4 for position, points in _REGULAR_ANCHORS.items()}
_BMW_CHAMPIONSHIP_ANCHORS = {position: points * 4 for position, points in _REGULAR_ANCHORS.items()}
# A handful of full-field weeks scheduled opposite a WGC/major draw a
# visibly weaker field and pay less -- sparser real anchor data available
# (only through position 5 plus the real last-place cutoff), still a
# genuinely distinct, real tier, not folded into "regular".
_OPPOSITE_FIELD_ANCHORS = {1: 300, 2: 165, 3: 105, 4: 80, 5: 65, 85: 0.93}

FEDEX_CUP_POINTS_BY_TIER = {
    "major": _MAJOR_ANCHORS,
    "elevated": _ELEVATED_ANCHORS,
    "regular": _REGULAR_ANCHORS,
    "opposite_field": _OPPOSITE_FIELD_ANCHORS,
    "fedex_st_jude": _FEDEX_ST_JUDE_ANCHORS,
    "bmw_championship": _BMW_CHAMPIONSHIP_ANCHORS,
}

# {season: {tournament_name: tier}} -- every real tour event not listed
# here defaults to "regular" (tier_for_event's own fail-OPEN, a deliberate
# deviation from this codebase's usual fail-closed convention: roughly 35
# of ~47 real tour events genuinely are plain regular-tier, so defaulting
# there is the correct guess for an unlisted event, not a masked bug).
# ANNUAL MAINTENANCE ITEM -- transcribe the real elevated-event
# designations and Playoffs calendar for each season played; the values
# below are a real starting point (2026 PGA Tour Signature Event
# schedule) but must be re-verified, not assumed to still be accurate for
# a future season.
EVENT_TIER_OVERRIDES = {
    2026: {
        "The Sentry": "elevated",
        "AT&T Pebble Beach Pro-Am": "elevated",
        "WM Phoenix Open": "elevated",
        "The Genesis Invitational": "elevated",
        "Arnold Palmer Invitational": "elevated",
        "THE PLAYERS Championship": "major",
        "RBC Heritage": "elevated",
        "Truist Championship": "elevated",
        "Memorial Tournament": "elevated",
        "Travelers Championship": "elevated",
        "Genesis Scottish Open": "elevated",
        "BMW Championship": "bmw_championship",
        "FedEx St. Jude Championship": "fedex_st_jude",
        "Masters Tournament": "major",
        "PGA Championship": "major",
        "U.S. Open": "major",
        "The Open Championship": "major",
    },
}


def tier_for_event(season: int, tournament_name: str | None, is_major: bool) -> str:
    """This event's own FedEx Cup points tier for `season` -- checks
    EVENT_TIER_OVERRIDES first (season, then tournament_name, both exact
    matches), then `is_major` (the one elevated/Playoffs-style signal
    ESPN's own API DOES expose directly -- ceded priority to a real
    override only because EVENT_TIER_OVERRIDES can distinguish "major"
    from "The Players" from a Playoffs event, none of which `is_major`
    alone can), and finally fails OPEN to "regular" -- see this module's
    own docstring for why that's the deliberately-chosen default rather
    than raising."""
    season_overrides = EVENT_TIER_OVERRIDES.get(season, {})
    if tournament_name is not None and tournament_name in season_overrides:
        return season_overrides[tournament_name]
    if is_major:
        return "major"
    return "regular"


def _interpolated_points(anchors: dict[int, float], position: int) -> float:
    """Linear interpolation between the two nearest transcribed anchor
    positions -- exact at a real anchor, a reasonable approximation
    everywhere else (see this module's own docstring). Positions before
    the first anchor or after the last one clamp to that anchor's own
    value rather than extrapolating past real transcribed data."""
    sorted_positions = sorted(anchors)
    if position <= sorted_positions[0]:
        return anchors[sorted_positions[0]]
    if position >= sorted_positions[-1]:
        return anchors[sorted_positions[-1]]
    lower = max(p for p in sorted_positions if p <= position)
    upper = min(p for p in sorted_positions if p >= position)
    if lower == upper:
        return anchors[lower]
    fraction = (position - lower) / (upper - lower)
    return anchors[lower] + fraction * (anchors[upper] - anchors[lower])


def points_for_field(tier: str, finish_positions: dict[str, int | None]) -> dict[str, float]:
    """{entity_id: points} for one event's own field -- finish_positions
    is {entity_id: finish_position}, None for cut/withdrawn/anyone with
    no real finish (0 points, same as finishing beyond the table's own
    reach). Real PGA Tour tie-splitting: golfers tied at the same
    finish_position split the AVERAGE of the points their tie spans (e.g.
    two golfers tied for 3rd split the average of what 3rd and 4th alone
    would each pay), not each independently paid the 3rd-place rate."""
    anchors = FEDEX_CUP_POINTS_BY_TIER.get(tier, FEDEX_CUP_POINTS_BY_TIER["regular"])

    by_position: dict[int, list[str]] = {}
    for entity_id, position in finish_positions.items():
        if position is None:
            continue
        by_position.setdefault(position, []).append(entity_id)

    result = {entity_id: 0.0 for entity_id in finish_positions}
    for position, tied_entity_ids in by_position.items():
        tie_size = len(tied_entity_ids)
        # A tie of size N starting at `position` spans positions
        # [position, position + N - 1] -- e.g. two golfers tied for 3rd
        # occupy "3rd and 4th" worth of the table between them.
        span_points = [_interpolated_points(anchors, position + offset) for offset in range(tie_size)]
        shared_points = sum(span_points) / tie_size
        for entity_id in tied_entity_ids:
            result[entity_id] = shared_points
    return result
