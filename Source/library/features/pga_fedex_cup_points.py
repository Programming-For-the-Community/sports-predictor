"""
Real FedEx Cup points table, hand-maintained -- same precedent as
library/features/nba_cup_groups.py. Anchor values below are the
2025/2026-season table, transcribed from
https://en.wikipedia.org/wiki/List_of_point_distributions_of_the_FedEx_Cup
-- ANNUAL MAINTENANCE ITEM: re-verify against the currently-published
table before trusting this for a new season; point values and which
tournaments count as "elevated" both change year to year.

ESPN's API exposes no elevated/Playoffs-tier signal (only a plain
`is_major` flag) -- EVENT_TIER_OVERRIDES below maps a season's real
calendar to a tier, keyed by tournament_name. tournament_name is null on
most older, already-completed events; pga_field_projection.py's
course_id-primary matching solves that separately.
"""

# Anchor points per tier, position -> points -- transcribed at these
# specific positions; every other position is linearly interpolated
# between its two nearest anchors (points_for_field below), an
# approximation for the un-transcribed middle positions.
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
# The two Playoffs events pay 4x regular-season points. TOUR Championship
# is excluded from this module entirely -- under the 2025+ format it
# awards no FedEx Cup points.
_FEDEX_ST_JUDE_ANCHORS = {position: points * 4 for position, points in _REGULAR_ANCHORS.items()}
_BMW_CHAMPIONSHIP_ANCHORS = {position: points * 4 for position, points in _REGULAR_ANCHORS.items()}
# A handful of full-field weeks scheduled opposite a WGC/major draw a
# weaker field and pay less -- sparser anchor data available (only
# through position 5 plus the last-place cutoff).
_OPPOSITE_FIELD_ANCHORS = {1: 300, 2: 165, 3: 105, 4: 80, 5: 65, 85: 0.93}

FEDEX_CUP_POINTS_BY_TIER = {
    "major": _MAJOR_ANCHORS,
    "elevated": _ELEVATED_ANCHORS,
    "regular": _REGULAR_ANCHORS,
    "opposite_field": _OPPOSITE_FIELD_ANCHORS,
    "fedex_st_jude": _FEDEX_ST_JUDE_ANCHORS,
    "bmw_championship": _BMW_CHAMPIONSHIP_ANCHORS,
}

# {season: {tournament_name: tier}} -- every event not listed here
# defaults to "regular" (tier_for_event's own fail-open; most tour events
# genuinely are regular-tier). ANNUAL MAINTENANCE ITEM -- re-verify the
# elevated-event designations and Playoffs calendar each season.
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
    """This event's FedEx Cup points tier for `season` -- checks
    EVENT_TIER_OVERRIDES first (season, then tournament_name), then
    `is_major`, and finally fails open to "regular"."""
    season_overrides = EVENT_TIER_OVERRIDES.get(season, {})
    if tournament_name is not None and tournament_name in season_overrides:
        return season_overrides[tournament_name]
    if is_major:
        return "major"
    return "regular"


def _interpolated_points(anchors: dict[int, float], position: int) -> float:
    """Linear interpolation between the two nearest transcribed anchor
    positions -- exact at a real anchor, approximate elsewhere. Positions
    before the first anchor or after the last one clamp to that anchor's
    value."""
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
    """{entity_id: points} for one event's field -- finish_positions is
    {entity_id: finish_position}, None for cut/withdrawn (0 points). Tied
    golfers split the average of the points their tie spans (e.g. two
    golfers tied for 3rd split the average of 3rd and 4th)."""
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
        # [position, position + N - 1].
        span_points = [_interpolated_points(anchors, position + offset) for offset in range(tie_size)]
        shared_points = sum(span_points) / tie_size
        for entity_id in tied_entity_ids:
            result[entity_id] = shared_points
    return result
