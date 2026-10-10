"""
Live score/status cache for NHL events currently in or near their
scheduled puck drop. Never writes to DynamoDB -- this is a short-lived,
UI-display-only cache in S3, refreshed on its own schedule
(scheduler-nhl-live-scores.tf, every 60s) and read back by GET
/nhl/live-scores (this same Lambda, see handler.py).

Thin wrapper around library.serving.live_scores_common. Hockey box scores
go through library.normalize.nhl's parser, so live stat keys match the
stored ones (goals, assists, shots_total, hits, saves). `refresh`/
`get_live_scores` are module attributes handler.py looks up at call time,
so tests can patch them.
"""
import logging

from library.normalize import nhl
from library.serving import live_scores_common as common
from library.serving.live_scores_common import POLL_SAFETY_CAP_AFTER_KICKOFF, POLL_START_BEFORE_KICKOFF, STALE_AFTER

logger = logging.getLogger("nhl-live-scores")

LIVE_SCORES_CACHE_KEY = "nhl/cache/live-scores/latest.json"

# A full slate is 16 games, most starting within the same hour.
BOXSCORE_MAX_WORKERS = 10

_parse_kickoff = common.parse_kickoff
_candidate_events = common.candidate_events
_extract_live_state = common.extract_live_state


_live = common.SportLiveScores(LIVE_SCORES_CACHE_KEY, {}, BOXSCORE_MAX_WORKERS, nhl.boxscore_to_player_game_stats)
_get_cache = _live.get_cache
_put_cache = _live.put_cache
_live_player_stats = _live.live_player_stats
refresh = _live.refresh
get_live_scores = _live.get_live_scores
