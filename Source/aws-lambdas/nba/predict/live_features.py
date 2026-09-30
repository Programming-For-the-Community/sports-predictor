"""
Assembles a live feature vector for one not-yet-played NBA event or
player-prop target, using the same pure functions (build_event_features/
build_player_features, library/features/nba.py) that build the training
datasets.

No team_coordinates parameter -- library.features.nba_teams is a static
30-franchise table, called directly. No presumptive-starter tracking --
there's no position whose own performance dominates a team's outcome in
basketball, so build_event_features carries no argument to populate here.

Leaders panel (build_live_event_leader_candidates) needs its own
per-category candidate search: no roster/depth-chart position data to
pick a starter from, so candidates are every still-rostered player
credited with the category's volume stat anywhere in the team's recent
box scores, ranked by recent volume. Basketball's own category set is
scoring/rebounding/assists (see library.serving.nba_reads' own
_STAT_CATEGORY).

`events` (an already-fetched get_all_events(sport) result) is an optional
pass-through accepted by every public function here and threaded into
get_team_events/_live_elo_ratings calls, so one event-prediction request
can share a single fetch instead of each function re-querying.
"""

from library.features import basketball_live
from library.features.live_orchestration import EventNotFoundError, MalformedEventError, box_score_candidate_ids
from library.features.nba import build_event_features, build_player_features

DEFAULT_ROLLING_WINDOW = 5
SEASON_LOOKBACK = 1

# Leaders-panel candidate generation -- box-score volume stat per category.
# Matches library.serving.nba_reads' own _CATEGORY_PRIMARY_STAT.
LEADER_VOLUME_STATS = basketball_live.LEADER_VOLUME_STATS
LEADER_CANDIDATE_LIMITS = basketball_live.LEADER_CANDIDATE_LIMITS


def _still_on_team(storage, sport: str, entity_id: str, team_id: str) -> bool:
    entity = storage.get_entity(sport, entity_id, "player")
    return bool(entity) and (entity.get("metadata") or {}).get("team_id") == team_id


def _box_score_candidate_ids(
    storage, sport: str, team_id: str, before_date: str, current_season: int | None, stat_key: str,
    events: list[dict] | None = None,
) -> list[str]:
    return box_score_candidate_ids(
        storage, sport, team_id, before_date, current_season, stat_key,
        season_lookback=SEASON_LOOKBACK, events=events,
        still_on_team=lambda entity_id: _still_on_team(storage, sport, entity_id, team_id),
    )


_features = basketball_live.BasketballLiveFeatures(
    build_event_features=build_event_features, build_player_features=build_player_features,
    candidate_ids=_box_score_candidate_ids, default_window=DEFAULT_ROLLING_WINDOW,
    team_game_stats_lookback_days=None,
)
_build_player_feature_row = _features.build_player_feature_row
build_live_event_features = _features.build_live_event_features
build_live_player_features = _features.build_live_player_features
build_live_event_leader_candidates = _features.build_live_event_leader_candidates
