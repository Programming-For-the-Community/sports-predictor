"""
F1 championship points -- driver points table + tie-splitting (shared
"average the points a tie spans" approach with
library/features/pga_fedex_cup_points.py's own points_for_field, since
F1 dead heats are essentially nonexistent but not ruled out by F1's own
rules), plus the fastest-lap bonus and constructor points, neither of
which has a PGA analog. Unlike PGA's own points module, no interpolation
machinery is needed here -- F1's real points table is small and fully
specified for every scoring position, not sparse.
"""

# Real F1 points table (2010-present scoring, still current as of this
# writing) -- position -> points, unlisted positions (11th on) score 0.
_POINTS_BY_POSITION = {1: 25, 2: 18, 3: 15, 4: 12, 5: 10, 6: 8, 7: 6, 8: 4, 9: 2, 10: 1}
# Sprint-session points (2022-present scoring; the 2021 debut season used
# a different, since-abandoned 3-point top-3-only table -- not modeled
# here, sprint backfill starts 2022 per the F1 onboarding plan).
_SPRINT_POINTS_BY_POSITION = {1: 8, 2: 7, 3: 6, 4: 5, 5: 4, 6: 3, 7: 2, 8: 1}

FASTEST_LAP_BONUS = 1.0
# Real F1 rule: the fastest-lap bonus only counts for a top-10 finisher.
FASTEST_LAP_MAX_POSITION = 10


def points_for_field(
    finish_positions: dict[str, int | None], *, sprint: bool = False, half_points: bool = False,
) -> dict[str, float]:
    """{entity_id: points} for one race's field. finish_positions is
    {entity_id: finish_position}, None for a non-classified result
    (dnf/dsq/dns -- see library/normalize/f1.py's map_status) which
    scores 0. Ties split by averaging the points the tied group spans
    (e.g. two drivers tied for 9th split the average of 9th and 10th's
    points), mirroring pga_fedex_cup_points.py's own tie-split logic.

    half_points -- rare, event-level (a red-flagged/shortened race under
    F1's own sporting regulations); halves every position's points, same
    "{pos: pts * K for pos, pts in base.items()}" shape
    pga_fedex_cup_points.py's own Playoffs-tier anchors already use."""
    table = _SPRINT_POINTS_BY_POSITION if sprint else _POINTS_BY_POSITION
    if half_points:
        table = {position: points / 2 for position, points in table.items()}

    by_position: dict[int, list[str]] = {}
    for entity_id, position in finish_positions.items():
        if position is None:
            continue
        by_position.setdefault(position, []).append(entity_id)

    result = {entity_id: 0.0 for entity_id in finish_positions}
    for position, tied_entity_ids in by_position.items():
        tie_size = len(tied_entity_ids)
        # A tie of size N starting at `position` spans positions
        # [position, position + N - 1]; a position past the scoring
        # table (11th on) contributes 0, same as an untied finisher there.
        span_points = [table.get(position + offset, 0) for offset in range(tie_size)]
        shared_points = sum(span_points) / tie_size
        for entity_id in tied_entity_ids:
            result[entity_id] = shared_points
    return result


def add_fastest_lap_bonus(
    points: dict[str, float], finish_positions: dict[str, int | None], fastest_lap_entity_id: str | None,
) -> dict[str, float]:
    """+1 point for the driver who set the race's fastest lap, IF they
    finished in the top 10 (real F1 rule) -- checked against
    finish_positions, not assumed from the points table alone, since a
    driver's fastest lap and their finish are independent facts fed in
    separately by the caller."""
    if fastest_lap_entity_id is None:
        return points
    position = finish_positions.get(fastest_lap_entity_id)
    if position is None or position > FASTEST_LAP_MAX_POSITION:
        return points
    updated = dict(points)
    updated[fastest_lap_entity_id] = updated.get(fastest_lap_entity_id, 0.0) + FASTEST_LAP_BONUS
    return updated


def constructor_points(driver_points: dict[str, float], driver_to_constructor: dict[str, str]) -> dict[str, float]:
    """Constructor points are literally the sum of both its drivers'
    points this race -- not a separate table, no PGA analog (golfers have
    no team). A driver missing from driver_to_constructor (shouldn't
    happen for a real race result, every driver has a constructor)
    contributes nothing rather than crashing."""
    totals: dict[str, float] = {}
    for driver_id, points in driver_points.items():
        constructor_id = driver_to_constructor.get(driver_id)
        if constructor_id is None:
            continue
        totals[constructor_id] = totals.get(constructor_id, 0.0) + points
    return totals
