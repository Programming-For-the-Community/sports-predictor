"""
Live score/status cache for NCAAFB events currently in or near their
scheduled kickoff. Deliberately never writes to DynamoDB: this is a
short-lived, UI-display-only cache in S3, refreshed on its own schedule
(scheduler-ncaafb-live-scores.tf, every 60s) and read back by GET
/ncaafb/live-scores (this same Lambda, see handler.py).

CFBD's own ids (game/team/player) share ESPN's exact numbering, so
refresh() can look up a candidate directly by event id in that day's
ESPN scoreboard response -- no crosswalk needed.

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

logger = logging.getLogger("ncaafb-live-scores")

LIVE_SCORES_CACHE_KEY = "ncaafb/cache/live-scores/latest.json"

_COMPOUND_KEY_SPLITS = espn.FOOTBALL_PLAYER_COMPOUND_KEY_SPLITS

# A Saturday with several games live at once shouldn't pay each event's
# own boxscore-fetch latency serially against this Lambda's own timeout.
BOXSCORE_MAX_WORKERS = 10

_parse_kickoff = common.parse_kickoff
_candidate_events = common.candidate_events
_extract_live_state = common.extract_live_state


_live = common.SportLiveScores(LIVE_SCORES_CACHE_KEY, _COMPOUND_KEY_SPLITS, BOXSCORE_MAX_WORKERS)
_get_cache = _live.get_cache
_put_cache = _live.put_cache
_live_player_stats = _live.live_player_stats
refresh = _live.refresh
get_live_scores = _live.get_live_scores
