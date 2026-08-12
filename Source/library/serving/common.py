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
        enriched.append({**row, "name": (entity or {}).get("name"), "abbreviation": metadata.get("abbreviation")})
    return enriched
