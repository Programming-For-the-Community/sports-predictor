"""
Fetches and caches one team's NFL depth chart, filtered down to the
positions leader-selection actually needs. Shared by
aws-lambdas/nfl/ingest/enrichment.py and aws-lambdas/nfl/schedule-sync/
handler.py so events from either path carry a real depth chart.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

DEPTH_CHART_POSITIONS = {"QB", "RB", "WR"}
DEPTH_CHART_CACHE_TTL_DAYS = 3


def filter_depth_chart(raw_depth_chart: dict) -> dict:
    """Keeps only the positions leader-selection actually needs (see
    DEPTH_CHART_POSITIONS), AND trims each retained athlete down to just
    the id live_features.py's _healthy_athlete_ids actually reads --
    ESPN's full depth chart response is ~289KB for ONE team, almost
    entirely per-athlete link metadata (player card, stats, splits, game
    log, news, bio -- each with a web AND a sportscenter:// deep-link
    variant) this project never uses.

    raw_depth_chart's own top level is `depthchart`, a list of formation-
    specific groups (e.g. "Base 3-4 D", "Special Teams", "3WR 1TE" --
    confirmed live), each with its OWN `positions` dict -- QB/RB/WR only
    ever appear in one offensive-formation group, so merging every
    group's positions into one flat result never collides for the
    positions this project keeps. Filters on each entry's
    position.abbreviation rather than the outer dict key, since that
    key's exact casing/format is only verified for non-skill-position
    codes (e.g. "lde" for Left Defensive End)."""
    result = {}
    for group in raw_depth_chart.get("depthchart") or []:
        for code, entry in (group.get("positions") or {}).items():
            abbreviation = (entry.get("position") or {}).get("abbreviation")
            if abbreviation not in DEPTH_CHART_POSITIONS:
                continue
            result[code] = {
                "position": {"abbreviation": abbreviation},
                "athletes": [{"id": athlete["id"]} for athlete in entry.get("athletes", []) if "id" in athlete],
            }
    return result


def _cache_key(team_id: str) -> str:
    return f"nfl/cache/depth-charts/{team_id}.json"


def home_away_team_ids(event: dict) -> tuple[str, str] | None:
    """competitions[0].competitors[]'s own team id/homeAway role, from a
    raw ESPN scoreboard event. None for a malformed event."""
    try:
        competitors = event["competitions"][0]["competitors"]
        home = next(c for c in competitors if c.get("homeAway") == "home")
        away = next(c for c in competitors if c.get("homeAway") == "away")
        return str(home["team"]["id"]), str(away["team"]["id"])
    except (KeyError, IndexError, StopIteration):
        return None


def attach_depth_charts(events: list[dict], nfl_client, s3, bucket: str) -> None:
    """Attaches home_depth_chart/away_depth_chart to each event dict in
    place, fetching each participating team's depth chart at most once
    regardless of how many events reference it. Best-effort per team -- a
    fetch failure just omits that team's field."""
    team_ids: set[str] = set()
    for event in events:
        ids = home_away_team_ids(event)
        if ids is not None:
            team_ids.update(ids)

    depth_chart_by_team: dict[str, dict] = {}
    for team_id in team_ids:
        try:
            depth_chart_by_team[team_id] = get_cached_depth_chart(s3, bucket, nfl_client, team_id)
        except Exception:
            logger.exception("Failed fetching depth chart for team %s -- depth chart field will be omitted", team_id)

    for event in events:
        ids = home_away_team_ids(event)
        if ids is None:
            continue
        home_id, away_id = ids
        event["home_depth_chart"] = depth_chart_by_team.get(home_id)
        event["away_depth_chart"] = depth_chart_by_team.get(away_id)


def get_cached_depth_chart(
    s3, bucket: str, nfl_client, team_id: str, ttl_days: int = DEPTH_CHART_CACHE_TTL_DAYS,
) -> dict:
    """Returns team_id's depth chart from the shared S3 cache if it was
    fetched within the last ttl_days, otherwise fetches fresh via
    nfl_client, caches it, and returns it. Same cache key regardless of
    caller, so ingest and schedule-sync share one fetch. A fetch failure
    propagates rather than falling back to a stale entry."""
    key = _cache_key(team_id)
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        cached = json.loads(response["Body"].read())
        fetched_at = datetime.fromisoformat(cached["fetched_at"])
        if datetime.now(timezone.utc) - fetched_at < timedelta(days=ttl_days):
            return cached["data"]
    except (ClientError, json.JSONDecodeError, KeyError):
        pass  # cache miss or malformed entry -- fetch fresh below

    data = filter_depth_chart(nfl_client.get_depth_chart(team_id))
    s3.put_object(
        Bucket=bucket, Key=key,
        Body=json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(), "data": data}),
        ContentType="application/json",
    )
    return data
