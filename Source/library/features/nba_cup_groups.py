"""
NBA in-season tournament ("NBA Cup") group assignments, keyed by season
(ESPN's own ending-year convention -- see nba_teams.py's docstring and
project-nba-onboarding memory's 2026-08-14 note for why NBA seasons are
labeled by the year they end in, not the year they start in). Unlike
TEAM_DIVISIONS (nba_teams.py), these are NOT stable -- the NBA redraws
Cup groups every season, seeded off the prior season's conference
standings. Update this table by hand once a year when the new season's
groups are announced (public, well before the season starts) -- same
"re-set once a season" maintenance pattern NCAAFB's own conference-
membership data needs, just done by hand here rather than pulled live.

WHY HARDCODED, NOT FETCHED: confirmed live, 2026-08-16, that ESPN's own
JSON APIs never expose group membership structurally -- a Cup game's
summary/scoreboard payload only ever carries the generic notes headline
"NBA Cup - Group Play" (see library/normalize/espn.py's tournament_note
field), with the actual group letter/name buried in unstructured article
prose, not a JSON field. The ONLY place real group assignments exist is
ESPN's human-rendered standings page (espn.com/nba/standings/_/view/
nba-cup) -- not a documented/versioned API, and scraping HTML in a
production Lambda is a materially worse reliability bet than this
project's other ESPN integrations (which all use its semi-stable JSON
endpoints): a page-template change would silently break group data with
no error, where a hardcoded table just goes stale in an obvious,
correctable way (a missing season key -- see cup_group_for_team below).

A season missing from this table means "not yet transcribed" -- NBA Cup
simulation degrades gracefully to skipped (None group for every team),
not an error. See aws-lambdas/nba/predict/season_simulation.py's own
simulate_cup for how that's handled.
"""

# {season: {conference: {group_letter: [team_id, ...]}}}. Team ids match
# nba_teams.py's own ESPN numeric ids. 2025-26 season (ESPN season.year
# 2026) confirmed live and re-confirmed unchanged against espn.com/nba/
# standings/_/view/nba-cup, both checks 2026-08-16 -- 3 groups of 5 per
# conference, 30 teams total.
CUP_GROUPS: dict[int, dict[str, dict[str, list[str]]]] = {
    2026: {
        "Eastern": {
            "A": ["28", "1", "5", "11", "27"],   # TOR, ATL, CLE, IND, WSH
            "B": ["19", "2", "8", "20", "17"],   # ORL, BOS, DET, PHI, BKN
            "C": ["18", "14", "15", "30", "4"],  # NY, MIA, MIL, CHA, CHI
        },
        "Western": {
            "A": ["25", "21", "16", "26", "23"],  # OKC, PHX, MIN, UTAH, SAC
            "B": ["13", "29", "12", "6", "3"],    # LAL, MEM, LAC, DAL, NO
            "C": ["24", "7", "10", "22", "9"],    # SA, DEN, HOU, POR, GS
        },
    },
}


def cup_group_for_team(season: int | None, team_id: str) -> str | None:
    """"Eastern A"/"Western C"/etc, or None if this season's groups
    haven't been added to CUP_GROUPS yet (see module docstring) or the
    team isn't found in any group for that season."""
    if season is None:
        return None
    groups = CUP_GROUPS.get(season)
    if not groups:
        return None
    for conference, conference_groups in groups.items():
        for group_letter, team_ids in conference_groups.items():
            if team_id in team_ids:
                return f"{conference} {group_letter}"
    return None
