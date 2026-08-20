"""
NBA in-season tournament ("NBA Cup") group assignments, keyed by season
(ESPN's own ending-year convention). Redrawn every season -- update by
hand once the new season's groups are announced. A season missing from
this table returns None (no group) for every team.
"""

# {season: {conference: {group_letter: [team_id, ...]}}}. Team ids match
# nba_teams.py's own ESPN numeric ids. 3 groups of 5 per conference, 30
# teams total.
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
    2027: {
        "Eastern": {
            "A": ["17", "8", "15", "19", "28"],  # BKN, DET, MIL, ORL, TOR
            "B": ["5", "11", "14", "18", "20"],  # CLE, IND, MIA, NY, PHI
            "C": ["1", "2", "30", "4", "27"],    # ATL, BOS, CHA, CHI, WSH
        },
        "Western": {
            "A": ["6", "7", "10", "21", "26"],   # DAL, DEN, HOU, PHX, UTAH
            "B": ["12", "29", "16", "3", "25"],  # LAC, MEM, MIN, NO, OKC
            "C": ["9", "13", "22", "23", "24"],  # GS, LAL, POR, SAC, SA
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
