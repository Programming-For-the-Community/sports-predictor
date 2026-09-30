"""
Assembles a live feature vector for one not-yet-played NCAA MBB event or
player-prop target, using the same pure functions (build_event_features/
build_player_features, library/features/ncaambb.py) that build the
training datasets. Byte-for-byte the same shape as nba/predict/
live_features.py -- see that module's own docstring for the reasoning
this one inherits unchanged.

No team_coordinates/travel_km parameter -- library.features.ncaambb has
none at all (see that module's own docstring: no geo-coordinate data
source exists for NCAA MBB). No presumptive-starter tracking -- there's
no position whose own performance dominates a team's outcome in
basketball, so build_event_features carries no argument to populate here.

Leaders panel (build_live_event_leader_candidates) needs its own
per-category candidate search: no roster/depth-chart position data to
pick a starter from, so candidates are every still-rostered player
credited with the category's volume stat anywhere in the team's recent
box scores, ranked by recent volume. Basketball's own category set is
scoring/rebounding/assists (see library.serving.ncaambb_reads' own
_STAT_CATEGORY). "Still-rostered" (_still_on_team) means both matches
team_id AND was recently reconfirmed there (_is_roster_entry_fresh) --
matching team_id alone isn't enough, since a player who's simply
stopped appearing anywhere (box scores or the daily roster re-fetch --
graduated, quit, transferred out) never gets an explicit removal write;
nothing ever un-sets their last-known team_id.

`events` (an already-fetched get_all_events(sport) result) is an optional
pass-through accepted by every public function here and threaded into
get_team_events/_live_elo_ratings calls, so one event-prediction request
can share a single fetch instead of each function re-querying.
"""
from datetime import date

from library.features import basketball_live
from library.features.live_orchestration import EventNotFoundError, MalformedEventError, box_score_candidate_ids
from library.features.live_orchestration import is_roster_entry_fresh
from library.features.ncaambb import build_event_features, build_player_features

DEFAULT_ROLLING_WINDOW = 5
SEASON_LOOKBACK = 1

# A player entity's metadata.team_id_as_of older than this many days is
# treated as "not recently confirmed on this team" rather than trusted
# indefinitely -- see _is_roster_entry_fresh. NCAA MBB's own ingest
# re-fetches every team's full roster on every daily run (ingest/
# handler.py's own _fetch_rosters -- "always fresh, never TTL-cached"),
# so this can stay tight, generous only enough to absorb a handful of
# missed runs -- matches nfl/predict/live_features.py's own
# _ROSTER_STALENESS_DAYS, which is tight for the same reason.
_ROSTER_STALENESS_DAYS = 14

# get_team_game_stats_for_team only keeps each team's DEFAULT_ROLLING_WINDOW
# most recent games -- bounding the underlying get_all_team_game_stats
# Query to this many days back (instead of every row ever written for the
# sport) keeps it a recent-window Query, not a full-season partition read.
# Generous relative to NCAA MBB's own ~2-3 games/week pace, including
# through a holiday break.
TEAM_GAME_STATS_LOOKBACK_DAYS = 60

# Leaders-panel candidate generation -- box-score volume stat per category.
# Matches library.serving.ncaambb_reads' own _CATEGORY_PRIMARY_STAT.
LEADER_VOLUME_STATS = basketball_live.LEADER_VOLUME_STATS
LEADER_CANDIDATE_LIMITS = basketball_live.LEADER_CANDIDATE_LIMITS


def _is_roster_entry_fresh(entity: dict, reference_date: str) -> bool:
    return is_roster_entry_fresh(entity, reference_date, _ROSTER_STALENESS_DAYS)


def _still_on_team(storage, sport: str, entity_id: str, team_id: str, reference_date: str | None = None) -> bool:
    """True only if entity_id's own team_id metadata both matches team_id
    AND was reconfirmed there recently (_is_roster_entry_fresh) -- a
    team_id match alone isn't enough, since nothing ever explicitly
    un-sets a player's last-known team_id once they stop appearing
    anywhere (see this module's own docstring)."""
    entity = storage.get_entity(sport, entity_id, "player")
    if not entity or (entity.get("metadata") or {}).get("team_id") != team_id:
        return False
    return _is_roster_entry_fresh(entity, reference_date or date.today().isoformat())


def _box_score_candidate_ids(
    storage, sport: str, team_id: str, before_date: str, current_season: int | None, stat_key: str,
    events: list[dict] | None = None, reference_date: str | None = None,
) -> list[str]:
    return box_score_candidate_ids(
        storage, sport, team_id, before_date, current_season, stat_key,
        season_lookback=SEASON_LOOKBACK, events=events,
        still_on_team=lambda entity_id: _still_on_team(storage, sport, entity_id, team_id, reference_date),
    )


_features = basketball_live.BasketballLiveFeatures(
    build_event_features=build_event_features, build_player_features=build_player_features,
    candidate_ids=_box_score_candidate_ids, default_window=DEFAULT_ROLLING_WINDOW,
    team_game_stats_lookback_days=TEAM_GAME_STATS_LOOKBACK_DAYS,
)
_build_player_feature_row = _features.build_player_feature_row
build_live_event_features = _features.build_live_event_features
build_live_player_features = _features.build_live_player_features
build_live_event_leader_candidates = _features.build_live_event_leader_candidates
