"""
DynamoDB key builders implementing design/DATA_SCHEMA.md's convention:
partition keys prefixed with SPORT#<sport>#... so per-sport queries stay
within one partition instead of scanning the whole table. Every sport
adapter's normalize step uses these -- not just NFL's -- so the key
format can't drift between sports.
"""


def entity_key(sport: str, entity_id: str) -> str:
    return f"SPORT#{sport.upper()}#ENTITY#{entity_id}"


def event_key(sport: str, event_id: str) -> str:
    return f"SPORT#{sport.upper()}#EVENT#{event_id}"


def player_key(sport: str, entity_id: str) -> str:
    return f"SPORT#{sport.upper()}#PLAYER#{entity_id}"


def team_key(team_id: str) -> str:
    return f"TEAM#{team_id}"


def entity_team_key(sport: str, team_id: str) -> str:
    """Groups entity items by current team for the entities table's
    team-index GSI (see Terraform/dynamodb-entities.tf) -- sport-scoped,
    unlike team_key() above, since a bare numeric ESPN team_id isn't
    unique across sports and this key is used as a global GSI hash key,
    not scoped within one event's own partition the way team_key() is."""
    return f"SPORT#{sport.upper()}#TEAM#{team_id}"


def sport_from_event_key(event_key: str) -> str:
    """Inverse of event_key() -- recovers the lowercase sport string from
    an existing SPORT#<SPORT>#EVENT#<event_id> key. Used by
    Source/migrations/backfill_sport_attribute.py to derive the sport
    attribute for rows written before player_game_stats/team_game_stats
    stored it directly -- every row already encodes it here, so this
    reads it back rather than needing any other data source."""
    return event_key.split("#")[1].lower()
