"""
NCAAFB's CFBD supplements as NCAAFB ingest saves them in the raw bucket,
each entry already tagged with our team_id:

    ncaafb/advanced/{season}/{season_type}/{week}.json  per-game advanced stats
    ncaafb/talent/{season}.json                         talent composite
    ncaafb/returning/{season}.json                      returning production

Feature engineering and live serving both read them through load(), so
they see the same values.
"""
import re
import time
from dataclasses import dataclass, field

from library.schema.keys import event_key

SPORT = "ncaafb"
ADVANCED_PREFIX = "ncaafb/advanced/"
TALENT_PREFIX = "ncaafb/talent/"
RETURNING_PREFIX = "ncaafb/returning/"
CACHE_TTL_SECONDS = 3600

_SEASON_IN_KEY = re.compile(r"^ncaafb/(?:advanced|talent|returning)/(\d{4})")


def advanced_key(season: int, season_type: str, week: int) -> str:
    return f"{ADVANCED_PREFIX}{season}/{season_type}/{week}.json"


def talent_key(season: int) -> str:
    return f"{TALENT_PREFIX}{season}.json"


def returning_key(season: int) -> str:
    return f"{RETURNING_PREFIX}{season}.json"


@dataclass
class CfbdSupplements:
    advanced: dict[tuple[str, str], dict] = field(default_factory=dict)  # (event_key, team_id) -> entry
    talent: dict[tuple[int, str], float] = field(default_factory=dict)  # (season, team_id) -> talent
    returning: dict[tuple[int, str], dict] = field(default_factory=dict)  # (season, team_id) -> entry


def _season(key: str) -> int | None:
    match = _SEASON_IN_KEY.match(key)
    return int(match.group(1)) if match else None


def _entries(raw_s3, prefix: str, seasons: set[int] | None):
    for key in raw_s3.list_keys(prefix):
        season = _season(key)
        if season is None or (seasons is not None and season not in seasons):
            continue
        payload = raw_s3.get_json(key)
        for entry in payload if isinstance(payload, list) else []:
            if isinstance(entry, dict) and entry.get("team_id") is not None:
                yield season, entry


def load(raw_s3, seasons: set[int] | None = None) -> CfbdSupplements:
    """Every saved supplement, or only those of `seasons`."""
    supplements = CfbdSupplements()
    for _, entry in _entries(raw_s3, ADVANCED_PREFIX, seasons):
        if entry.get("gameId") is not None:
            supplements.advanced[(event_key(SPORT, str(entry["gameId"])), str(entry["team_id"]))] = entry
    for season, entry in _entries(raw_s3, TALENT_PREFIX, seasons):
        talent = entry.get("talent")
        if isinstance(talent, (int, float)):
            supplements.talent[(season, str(entry["team_id"]))] = float(talent)
    for season, entry in _entries(raw_s3, RETURNING_PREFIX, seasons):
        supplements.returning[(season, str(entry["team_id"]))] = entry
    return supplements


_cache: dict[frozenset, tuple[float, CfbdSupplements]] = {}


def load_cached(raw_s3, seasons: set[int]) -> CfbdSupplements:
    """load() for serving, reused for CACHE_TTL_SECONDS in a warm container."""
    key = frozenset(seasons)
    cached = _cache.get(key)
    if cached is None or time.monotonic() - cached[0] >= CACHE_TTL_SECONDS:
        cached = (time.monotonic(), load(raw_s3, seasons))
        _cache[key] = cached
    return cached[1]


def attach_advanced(team_game_stats: list[dict], supplements: CfbdSupplements) -> list[dict]:
    """team_game_stats rows with each row's own advanced entry under "advanced"."""
    return [
        {**row, "advanced": supplements.advanced.get((row.get("event_key"), str(row.get("team_id"))))}
        for row in team_game_stats
    ]
