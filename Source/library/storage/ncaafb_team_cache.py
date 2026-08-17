"""
Fetches and caches CFBD's season-scoped team list, and applies the one
piece of team-derived enrichment cheap and stable enough to run from
BOTH ingest and schedule-sync: venue_indoor. Shared here (not left inside
aws-lambdas/ncaafb/ingest/enrichment.py) for the same reason NFL's own
depth_chart_cache.py is shared rather than living under aws-lambdas/nfl/
ingest/ -- Lambda deployment packages are built per-function (see
python_lambda_build.yml), so schedule-sync/handler.py can only import
modules from this shared library package, never a sibling Lambda's own
local file.

Coach and ranking enrichment stay local to ingest/enrichment.py instead
(ingest-only, same split NFL draws between this module and its own
ingest-only coach/injury logic) -- they're keyed by school name via this
module's own get_cached_teams, but the resulting lookup dicts aren't
reused by schedule-sync, unlike venue_indoor.
"""
import json
from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError

TEAMS_CACHE_TTL_DAYS = 7


def _teams_cache_key(season: int) -> str:
    return f"ncaafb/cache/season-teams/{season}.json"


def get_cached_teams(s3, bucket: str, client, season: int) -> list[dict]:
    """Every FBS team for `season` -- TTL-cached (TEAMS_CACHE_TTL_DAYS).
    The join table CFBD enrichment resolves through everywhere: /games
    only has numeric home_id/away_id, while /coaches and /rankings only
    have school names."""
    key = _teams_cache_key(season)
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        cached = json.loads(response["Body"].read())
        fetched_at = datetime.fromisoformat(cached["fetched_at"])
        if datetime.now(timezone.utc) - fetched_at < timedelta(days=TEAMS_CACHE_TTL_DAYS):
            return cached["data"]
    except (ClientError, json.JSONDecodeError, KeyError):
        pass  # cache miss or malformed entry -- fetch fresh below

    data = client.get_teams(season)
    s3.put_object(
        Bucket=bucket, Key=key,
        Body=json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(), "data": data}),
        ContentType="application/json",
    )
    return data


def teams_by_id(teams: list[dict]) -> dict[str, dict]:
    return {str(t["id"]): t for t in teams if t.get("id") is not None}


def teams_by_school(teams: list[dict]) -> dict[str, dict]:
    return {t["school"]: t for t in teams if t.get("school")}


def attach_venue_indoor(games: list[dict], season: int, client, s3, bucket: str) -> None:
    """Attaches venue_indoor/venue_city/venue_state to each game dict in
    place, from the home team's own venue -- CFBD's own /games response
    carries only a bare venue name string, no city/state breakdown (a
    separate /venues-by-id endpoint has that, but the home team's own
    /teams `location` is already this same Venue object -- confirmed live
    via CFBD's own OpenAPI schema -- so no second endpoint/cache is
    needed). Same "home team's own listed venue" approximation
    venue_indoor already made (not exact for a real neutral-site game),
    same reasoning. Cheap enough (one TTL-cached bulk call) to run
    unconditionally from schedule-sync's season-wide walk, unlike coach/
    ranking enrichment (ingest-only, see aws-lambdas/ncaafb/ingest/
    enrichment.py)."""
    by_id = teams_by_id(get_cached_teams(s3, bucket, client, season))
    for game in games:
        home_id = str(game["homeId"]) if game.get("homeId") is not None else None
        home_team = by_id.get(home_id) if home_id else None
        location = (home_team or {}).get("location") or {}
        game["venue_indoor"] = location.get("dome")
        game["venue_city"] = location.get("city")
        game["venue_state"] = location.get("state")
