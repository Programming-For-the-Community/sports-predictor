"""
Live score/status cache for NCAA MBB events currently in or near their
scheduled tip-off. Never writes to DynamoDB -- this is a short-lived,
UI-display-only cache in S3, refreshed on its own schedule
(scheduler-ncaambb-live-scores.tf, every 60s) and read back by GET
/ncaambb/live-scores (this same Lambda, see handler.py).

NCAA MBB's data source and live-scores source are both ESPN, so refresh()
looks up a candidate directly by event id in that day's ESPN scoreboard
response, no abbreviation+date join fallback needed.

Thin wrapper around library.serving.live_scores_common (confirmed
byte-identical logic across nfl/nba/ncaafb/ncaambb before sharing there) --
only this sport's own cache key/compound-stat-key shape/worker count stay
here. `refresh`/`get_live_scores` stay real module-level functions (not
just re-exported common.* names) since handler.py's own tests patch them
by attribute.
"""
import logging

from library.serving import live_scores_common as common
from library.serving.live_scores_common import POLL_SAFETY_CAP_AFTER_KICKOFF, POLL_START_BEFORE_KICKOFF, STALE_AFTER

logger = logging.getLogger("ncaambb-live-scores")

LIVE_SCORES_CACHE_KEY = "ncaambb/cache/live-scores/latest.json"

# Same compound-key shape as aws-lambdas/ncaambb/normalize/handler.py's own
# _COMPOUND_KEY_SPLITS (basketball -- identical to NBA's) -- duplicated
# here rather than imported across Lambda package boundaries. Must stay in
# sync with normalize's copy.
_COMPOUND_KEY_SPLITS: dict[str, tuple[str, str]] = {
    "fieldGoalsMade-fieldGoalsAttempted": ("field_goals_made", "field_goal_attempts"),
    "threePointFieldGoalsMade-threePointFieldGoalsAttempted": ("three_pointers_made", "three_point_attempts"),
    "freeThrowsMade-freeThrowsAttempted": ("free_throws_made", "free_throw_attempts"),
}

# NCAA MBB can have 100+ tip-offs clustered on a given night -- the
# largest per-night volume of any sport onboarded so far (see
# project-ncaambb-onboarding memory), so this is raised above NBA's 10.
# Still below ingest's own _INGEST_MAX_WORKERS=8-per-call-type cap's spirit
# -- 15 is a deliberate middle ground, not the full plausible peak overlap.
BOXSCORE_MAX_WORKERS = 15

_parse_kickoff = common.parse_kickoff
_candidate_events = common.candidate_events
_extract_live_state = common.extract_live_state


def _get_cache(s3, bucket: str) -> dict | None:
    return common.get_cache(s3, bucket, LIVE_SCORES_CACHE_KEY)


def _put_cache(s3, bucket: str, payload: dict) -> None:
    common.put_cache(s3, bucket, LIVE_SCORES_CACHE_KEY, payload)


def _live_player_stats(client, sport: str, event_id: str) -> dict[str, dict]:
    return common.live_player_stats(client, sport, event_id, _COMPOUND_KEY_SPLITS)


def refresh(storage, s3, bucket: str, client, sport: str) -> dict:
    return common.refresh(storage, s3, bucket, client, sport, LIVE_SCORES_CACHE_KEY, _COMPOUND_KEY_SPLITS, BOXSCORE_MAX_WORKERS)


def get_live_scores(s3, bucket: str) -> dict:
    return common.get_live_scores(s3, bucket, LIVE_SCORES_CACHE_KEY)
