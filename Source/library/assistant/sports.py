"""
The sports the assistant knows, read from the sport registry table
(Terraform/dynamodb-sport-registry.tf) -- adding a sport's registry row adds
it to the assistant. Cached per container for REFRESH_SECONDS.
"""
from __future__ import annotations

import time

REFRESH_SECONDS = 3600
FIELD = "field"

_cache: dict[str, tuple[float, dict[str, str]]] = {}


def _event_types(table) -> dict[str, str]:
    """sport -> its registry event_type ("head_to_head" or "field")."""
    cached = _cache.get(table.name)
    if cached and time.monotonic() - cached[0] < REFRESH_SECONDS:
        return cached[1]
    items: list[dict] = []
    kwargs = {"ProjectionExpression": "sport, event_type"}
    while True:
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    types = {item["sport"]: item.get("event_type", "") for item in items if item.get("sport")}
    _cache[table.name] = (time.monotonic(), types)
    return types


def load_sports(table) -> list[str]:
    """Sorted sport keys ("nfl", "nba", ...) from every registry row."""
    return sorted(_event_types(table))


def load_field_sports(table) -> frozenset[str]:
    """The sports whose events are a field of individual competitors (golf, racing)."""
    return frozenset(sport for sport, kind in _event_types(table).items() if kind == FIELD)


def clear_cache() -> None:
    _cache.clear()
