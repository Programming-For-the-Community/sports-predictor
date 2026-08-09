"""
Fetches and caches CFBD's season-scoped coach list, plus the school-name
lookup builders for coaches and AP Top 25 rankings -- shared here (not
left inside aws-lambdas/ncaafb/ingest/enrichment.py) for the same reason
library/storage/ncaafb_team_cache.py already is: Lambda deployment
packages are built per-function, and data-backfills/ncaafb/backfill.py
(an ECS task, not a Lambda) needs this exact same season-scoped
coach/rank resolution to enrich 10 seasons of historical games, so it can
no longer stay a sibling Lambda's local file.

enrich_games itself (the per-week orchestration that calls these) stays
in aws-lambdas/ncaafb/ingest/enrichment.py -- only the school-keyed
lookup builders and the coach cache move here, mirroring the split
ncaafb_team_cache.py's own docstring describes between shared caching/
lookup and ingest-only orchestration.
"""
import json
from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError

COACHES_CACHE_TTL_DAYS = 7


def _coaches_cache_key(season: int) -> str:
    return f"ncaafb/cache/season-coaches/{season}.json"


def get_cached_coaches(s3, bucket: str, client, season: int) -> list[dict]:
    """Every FBS coach for `season` -- TTL-cached (COACHES_CACHE_TTL_DAYS),
    same cadence reasoning as get_cached_teams: a coach's identity and
    season record barely change day to day, so repeated calls within the
    TTL window are cache hits after the first one."""
    key = _coaches_cache_key(season)
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        cached = json.loads(response["Body"].read())
        fetched_at = datetime.fromisoformat(cached["fetched_at"])
        if datetime.now(timezone.utc) - fetched_at < timedelta(days=COACHES_CACHE_TTL_DAYS):
            return cached["data"]
    except (ClientError, json.JSONDecodeError, KeyError):
        pass  # cache miss or malformed entry -- fetch fresh below

    data = client.get_coaches(season)
    s3.put_object(
        Bucket=bucket, Key=key,
        Body=json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(), "data": data}),
        ContentType="application/json",
    )
    return data


def coach_lookup_by_school(coaches: list[dict], season: int) -> dict[str, dict]:
    """{school: {"coach_name", "coach_experience", "season_win_pct"}} for
    every coach with a season entry matching `season`. No
    career_playoff_win_pct equivalent -- CFBD folds bowl/playoff results
    into the same season win-loss totals rather than breaking them out
    separately, unlike ESPN's dedicated career-postseason-record endpoint
    (see library/http/espn_core.py), so that field is genuinely
    unavailable here rather than omitted by choice."""
    lookup: dict[str, dict] = {}
    for coach in coaches:
        season_entry = next((s for s in coach.get("seasons", []) if s.get("year") == season), None)
        if season_entry is None or not season_entry.get("school"):
            continue

        wins = season_entry.get("wins") or 0
        losses = season_entry.get("losses") or 0
        ties = season_entry.get("ties") or 0
        decided = wins + losses + ties

        hire_date = coach.get("hireDate")
        experience = (season - int(hire_date[:4])) if hire_date else None

        lookup[season_entry["school"]] = {
            "coach_name": f"{coach.get('firstName', '')} {coach.get('lastName', '')}".strip() or None,
            "coach_experience": experience,
            "season_win_pct": (wins / decided) if decided else None,
        }
    return lookup


def rank_lookup_by_school(rankings: list[dict]) -> dict[str, int]:
    """{school: rank} from the "AP Top 25" poll only, for the first week
    entry that has one -- CFBD's /rankings response bundles FCS/other-
    division polls in the same payload (see library/http/cfbd.py's
    get_rankings docstring), so polls[0] can't be assumed to be the AP
    poll. Callers scoped to a single week (ingest) pass a single-entry
    list; callers building season-wide history (backfill) call this once
    per week entry instead of once for the whole season, since each
    week's AP poll ranks different teams."""
    for week_entry in rankings:
        for poll in week_entry.get("polls", []):
            if poll.get("poll") == "AP Top 25":
                return {r["school"]: r["rank"] for r in poll.get("ranks", []) if r.get("school") and r.get("rank")}
    return {}
