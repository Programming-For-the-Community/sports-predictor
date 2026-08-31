"""
F1-specific normalization: thin wrappers over library.normalize.f1 that
bind the sport string, same pattern as every other sport's backfill
normalize.py (e.g. data-backfills/pga/normalize.py).

merge_qualifying_into_event is re-exported unchanged (no sport binding
needed -- it operates on an already-built event item, not a raw payload).
"""
from library.normalize.f1 import (
    merge_qualifying_into_event,
    race_result_to_constructor_entities as _race_result_to_constructor_entities,
    race_result_to_driver_entities as _race_result_to_driver_entities,
    race_result_to_event_item as _race_result_to_event_item,
    sprint_result_to_constructor_entities as _sprint_result_to_constructor_entities,
    sprint_result_to_driver_entities as _sprint_result_to_driver_entities,
    sprint_result_to_event_item as _sprint_result_to_event_item,
)

SPORT = "f1"


def race_result_to_event_item(payload: dict) -> dict:
    return _race_result_to_event_item(payload, SPORT)


def race_result_to_driver_entities(payload: dict) -> list[dict]:
    return _race_result_to_driver_entities(payload, SPORT)


def race_result_to_constructor_entities(payload: dict) -> list[dict]:
    return _race_result_to_constructor_entities(payload, SPORT)


def sprint_result_to_event_item(payload: dict) -> dict:
    return _sprint_result_to_event_item(payload, SPORT)


def sprint_result_to_driver_entities(payload: dict) -> list[dict]:
    return _sprint_result_to_driver_entities(payload, SPORT)


def sprint_result_to_constructor_entities(payload: dict) -> list[dict]:
    return _sprint_result_to_constructor_entities(payload, SPORT)
