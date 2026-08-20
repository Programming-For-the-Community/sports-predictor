"""
Sport-agnostic serving helpers shared across sports' *_reads.py modules.
"""


def enrich_participants(storage, sport: str, participants: list[dict] | None) -> list[dict] | None:
    """Attaches each participant's own team entity name/abbreviation/
    conference/color. One get_entity per participant."""
    if not participants:
        return participants

    enriched = []
    for participant in participants:
        entity = storage.get_entity(sport, participant["entity_id"], "team")
        metadata = (entity or {}).get("metadata") or {}
        enriched.append({
            **participant,
            "name": (entity or {}).get("name"),
            "abbreviation": metadata.get("abbreviation"),
            "conference": metadata.get("conference"),
            "color": metadata.get("color"),
        })
    return enriched


def enrich_team_standings(storage, sport: str, standings: list[dict]) -> list[dict]:
    """Same purpose as enrich_participants, for standings rows (keyed by
    team_id, no role/result)."""
    enriched = []
    for row in standings:
        entity = storage.get_entity(sport, row["team_id"], "team")
        metadata = (entity or {}).get("metadata") or {}
        enriched.append({
            **row,
            "name": (entity or {}).get("name"),
            "abbreviation": metadata.get("abbreviation"),
            "color": metadata.get("color"),
        })
    return enriched


def enrich_bracket_team_names(storage, sport: str, bracket: dict) -> dict:
    """Attaches a {team_id: {"name", "abbreviation", "color"}} lookup
    (`team_names`) to a bracket payload -- collects every distinct team id
    across all matchup rows and looks each up exactly once."""
    team_ids: set[str] = set()

    def _collect_matchup(matchup: dict) -> None:
        for key in ("team_a", "team_b"):
            if matchup.get(key):
                team_ids.add(matchup[key])

    def _collect_rounds(rounds: list[dict]) -> None:
        for round_ in rounds:
            for matchup in round_["matchups"]:
                _collect_matchup(matchup)

    for rounds in bracket.get("conferences", {}).values():
        _collect_rounds(rounds)
    if bracket.get("rounds"):
        _collect_rounds(bracket["rounds"])
    for key in ("super_bowl", "finals", "championship"):
        if bracket.get(key):
            _collect_matchup(bracket[key])

    team_names = {}
    for team_id in team_ids:
        entity = storage.get_entity(sport, team_id, "team")
        metadata = (entity or {}).get("metadata") or {}
        team_names[team_id] = {
            "name": (entity or {}).get("name"),
            "abbreviation": metadata.get("abbreviation"),
            "color": metadata.get("color"),
        }

    return {**bracket, "team_names": team_names}
