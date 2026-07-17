"""
NFL-specific entry point for the shared pipeline storage wiring -- see
library.storage.pipeline_storage.PipelineStorage. All tables/buckets are
shared across sports (design/DATA_SCHEMA.md), so there's nothing
NFL-specific left to wire here; this file exists only so backfill.py has
a stable set of function names to call. The next sport's backfill can
copy this file verbatim, or skip it and use PipelineStorage directly.
"""
from library.storage.pipeline_storage import PipelineStorage

_storage = PipelineStorage()

RAW_BUCKET = _storage.raw_bucket


def raw_object_exists(key: str) -> bool:
    return _storage.raw_object_exists(key)


def put_raw_json(key: str, payload: dict) -> None:
    _storage.put_raw_json(key, payload)


def upsert_entity(item: dict) -> None:
    _storage.upsert_entity(item)


def upsert_event(item: dict) -> None:
    _storage.upsert_event(item)


def write_player_game_stats(items: list[dict]) -> None:
    _storage.write_player_game_stats(items)
