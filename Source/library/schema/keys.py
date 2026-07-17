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


def player_key(entity_id: str) -> str:
    return f"PLAYER#{entity_id}"
