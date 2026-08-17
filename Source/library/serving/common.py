"""
Sport-agnostic serving helpers shared by nfl_reads.py and ncaafb_reads.py.
Kept separate from either (same reasoning as library.features.common) so
ncaafb_reads.py never has to import an NFL-named module.
"""


def enrich_participants(storage, sport: str, participants: list[dict] | None) -> list[dict] | None:
    """Attaches each participant's own team entity name/abbreviation/
    conference -- the frontend has no other source for team display text
    (see front-end/lib/static/nfl_team_colors.dart's own docstring: NFL's
    table is hand-maintained and never needed this, but no such table
    exists for any other sport) or for conference (used to group a large
    event list, see event_list_page.dart's own grouping -- null for NFL,
    whose team entities carry no conference field at all, so NFL's list
    stays date-only). One get_entity per participant; events from
    list_events are already scoped to a single week, so this is at most
    a couple dozen extra GetItems per call."""
    if not participants:
        return participants

    enriched = []
    for participant in participants:
        entity = storage.get_entity(sport, participant["entity_id"])
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
    """Same purpose as enrich_participants (the frontend has no other
    source for team display text), but standings rows aren't
    Participant-shaped -- keyed by team_id, no role/result -- so this is
    its own function rather than a reuse of that one. Runs inside the
    weekly-scheduled season projection job (season_projection.py's
    build_season_projection), not a per-request Lambda, so one get_entity
    per row is cheap regardless of team count."""
    enriched = []
    for row in standings:
        entity = storage.get_entity(sport, row["team_id"])
        metadata = (entity or {}).get("metadata") or {}
        enriched.append({
            **row,
            "name": (entity or {}).get("name"),
            "abbreviation": metadata.get("abbreviation"),
            "color": metadata.get("color"),
        })
    return enriched


def enrich_bracket_team_names(storage, sport: str, bracket: dict) -> dict:
    """Attaches a {team_id: {"name", "abbreviation"}} lookup (`team_names`)
    to a bracket payload (see aws-lambdas/*/predict/season_projection.py's
    own _bracket_payload/_cup_bracket_payload) -- same "the frontend has
    no other source for team display text" reasoning as
    enrich_team_standings above, but a bracket's own team ids are
    scattered across many matchup rows (team_a/team_b, one row per game)
    rather than one row per team, so this collects every distinct id
    across the whole bracket first and looks each up exactly once,
    instead of once per matchup appearance."""
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
        entity = storage.get_entity(sport, team_id)
        metadata = (entity or {}).get("metadata") or {}
        team_names[team_id] = {
            "name": (entity or {}).get("name"),
            "abbreviation": metadata.get("abbreviation"),
            "color": metadata.get("color"),
        }

    return {**bracket, "team_names": team_names}
