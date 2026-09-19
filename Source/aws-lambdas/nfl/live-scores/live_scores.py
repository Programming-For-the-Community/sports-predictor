"""
Live score/status cache for events currently in or near their scheduled
kickoff. Never writes to DynamoDB -- this is a short-lived, UI-display-
only cache in S3, refreshed on its own schedule (scheduler-nfl-live-
scores.tf, every 60s) and read back by GET /nfl/live-scores (handler.py).

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

logger = logging.getLogger("nfl-live-scores")

LIVE_SCORES_CACHE_KEY = "nfl/cache/live-scores/latest.json"

# Compound-stat key shape for a completed event's box score. Must stay in
# sync with normalize/handler.py's own copy.
_COMPOUND_KEY_SPLITS: dict[str, tuple[str, str]] = {
    "completions/passingAttempts": ("completions", "passing_attempts"),
    "sacks-sackYardsLost": ("sacks_taken", "sack_yards_lost"),
    "fieldGoalsMade/fieldGoalAttempts": ("field_goals_made", "field_goal_attempts"),
    "extraPointsMade/extraPointAttempts": ("extra_points_made", "extra_point_attempts"),
}

# Max parallel ESPN summary/boxscore fetches for currently-live events.
BOXSCORE_MAX_WORKERS = 10

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
