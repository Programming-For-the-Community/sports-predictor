"""
DynamoDB key builders: partition keys prefixed with SPORT#<sport>#... so
per-sport queries stay within one partition.
"""


def entity_key(sport: str, entity_id: str, entity_type: str) -> str:
    """entity_type ("team" or "player") is part of the key -- team-id and
    athlete-id numeric spaces can overlap, so the type disambiguates."""
    return f"SPORT#{sport.upper()}#ENTITY#{entity_type.upper()}#{entity_id}"


def event_key(sport: str, event_id: str) -> str:
    return f"SPORT#{sport.upper()}#EVENT#{event_id}"


def player_key(sport: str, entity_id: str) -> str:
    return f"SPORT#{sport.upper()}#PLAYER#{entity_id}"


def team_key(team_id: str) -> str:
    return f"TEAM#{team_id}"


def entity_team_key(sport: str, team_id: str) -> str:
    """Sport-scoped team key for the entities table's team-index GSI --
    a bare numeric team_id isn't unique across sports."""
    return f"SPORT#{sport.upper()}#TEAM#{team_id}"


def sport_from_event_key(event_key: str) -> str:
    """Inverse of event_key() -- recovers the lowercase sport string from
    a SPORT#<SPORT>#EVENT#<event_id> key."""
    return event_key.split("#")[1].lower()
