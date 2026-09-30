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
here. `refresh`/`get_live_scores` are module attributes handler.py looks
up at call time, so tests can patch them.
"""
import logging

from library.normalize import espn
from library.serving import live_scores_common as common
from library.serving.live_scores_common import POLL_SAFETY_CAP_AFTER_KICKOFF, POLL_START_BEFORE_KICKOFF, STALE_AFTER

logger = logging.getLogger("ncaambb-live-scores")

LIVE_SCORES_CACHE_KEY = "ncaambb/cache/live-scores/latest.json"

_COMPOUND_KEY_SPLITS = espn.BASKETBALL_COMPOUND_KEY_SPLITS

# NCAA MBB can have 100+ tip-offs clustered on a given night -- the
# largest per-night volume of any sport onboarded so far (see
# project-ncaambb-onboarding memory), so this is raised above NBA's 10.
# Still below ingest's own _INGEST_MAX_WORKERS=8-per-call-type cap's spirit
# -- 15 is a deliberate middle ground, not the full plausible peak overlap.
BOXSCORE_MAX_WORKERS = 15

_parse_kickoff = common.parse_kickoff
_candidate_events = common.candidate_events
_extract_live_state = common.extract_live_state


_live = common.SportLiveScores(LIVE_SCORES_CACHE_KEY, _COMPOUND_KEY_SPLITS, BOXSCORE_MAX_WORKERS)
_get_cache = _live.get_cache
_put_cache = _live.put_cache
_live_player_stats = _live.live_player_stats
refresh = _live.refresh
get_live_scores = _live.get_live_scores
