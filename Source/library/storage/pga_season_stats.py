"""
PGA season-stats snapshot reading -- extracted, unchanged, from
feature-engineering/pga/build_dataset.py 2026-08-27 so aws-lambdas/pga/
predict/live_features.py can resolve the same live-season-stats signal
training already reads, without importing a Fargate-task module.

ESPN's own golf/pga/statistics endpoint is CURRENT-SNAPSHOT-ONLY (its
season/year query params are silently ignored, confirmed live
2026-08-25) -- pga-ingest's own daily raw snapshot (pga/statistics/
{date}.json) is the ONLY source of historical values for these
categories, there is no backfill path for it at all (design/
DATA_SCHEMA.md).
"""
import re

from library.aws.s3_manager import S3Manager
from library.features.pga import SEASON_STAT_CATEGORIES

_STATISTICS_KEY_RE = re.compile(r"pga/statistics/(\d{8})\.json$")


def load_season_stat_snapshots(raw_s3: S3Manager) -> list[dict]:
    """Every season-stats snapshot pga-ingest has written. Returns
    [{"as_of_date", "value_by_category_and_athlete"}, ...], oldest first
    -- same overall shape/reasoning as NCAA MBB's own _load_rankings for
    its AP polls."""
    snapshots = []
    for key in raw_s3.list_keys("pga/statistics/"):
        match = _STATISTICS_KEY_RE.match(key)
        if not match:
            continue
        raw_date = match.group(1)
        as_of_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
        payload = raw_s3.get_json(key)
        value_by_category_and_athlete: dict[str, dict[str, float]] = {}
        for category in payload.get("stats", {}).get("categories", []):
            name = category.get("name")
            if name not in SEASON_STAT_CATEGORIES:
                continue
            value_by_category_and_athlete[name] = {
                leader["athlete"]["id"]: leader["value"]
                for leader in category.get("leaders", [])
                if leader.get("athlete", {}).get("id") is not None and leader.get("value") is not None
            }
        snapshots.append({"as_of_date": as_of_date, "value_by_category_and_athlete": value_by_category_and_athlete})
    snapshots.sort(key=lambda s: s["as_of_date"])
    return snapshots


def resolve_season_stats(snapshots: list[dict], entity_id: str, before_date: str) -> dict[str, float | None]:
    """This golfer's own values from the MOST RECENT snapshot STRICTLY
    BEFORE `before_date` -- never same-day-or-later, which would leak
    this tournament's own in-progress state into its own features. None
    for every category if no qualifying snapshot exists yet or the
    golfer wasn't in that category's top 50 that day."""
    candidates = [s for s in snapshots if s["as_of_date"] < before_date]
    if not candidates:
        return {category: None for category in SEASON_STAT_CATEGORIES}
    latest = candidates[-1]  # snapshots is ascending -> the last qualifying one is the most recent
    return {
        category: latest["value_by_category_and_athlete"].get(category, {}).get(entity_id)
        for category in SEASON_STAT_CATEGORIES
    }
