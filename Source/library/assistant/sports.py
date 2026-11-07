"""
The sports the assistant knows, read from the sport registry table
(Terraform/dynamodb-sport-registry.tf) -- adding a sport's registry row adds
it to the assistant. Cached per container for REFRESH_SECONDS.
"""
from __future__ import annotations

import time

REFRESH_SECONDS = 3600

_cache: dict[str, tuple[float, list[str]]] = {}


def load_sports(table) -> list[str]:
    """Sorted sport keys ("nfl", "nba", ...) from every registry row."""
    cached = _cache.get(table.name)
    if cached and time.monotonic() - cached[0] < REFRESH_SECONDS:
        return cached[1]
    items: list[dict] = []
    kwargs = {"ProjectionExpression": "sport"}
    while True:
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    sports = sorted({item["sport"] for item in items if item.get("sport")})
    _cache[table.name] = (time.monotonic(), sports)
    return sports


def clear_cache() -> None:
    _cache.clear()
