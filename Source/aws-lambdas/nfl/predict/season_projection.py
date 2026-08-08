"""
Season-long standings/leaderboard orchestration -- builds the payload
Terraform/scheduler-nfl-season-projection.tf's weekly EventBridge
Scheduler invoke writes to S3 for GET /nfl/season (see handler.py's own
docstring for why that route can't compute this live per-request). Pulls
season-wide data once via FeatureStorage, derives Elo ratings and
remaining-schedule inputs (_season_standings_inputs), runs
season_simulation's pure Monte Carlo logic, and scores each tracked
player-prop leaderboard (_leaderboards) using the same model-loading
helpers event_prediction.py uses for a single live request.
"""
import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import event_prediction
import live_features
import model_loader
import season_simulation
from library.features.common import compute_elo_ratings
from library.features.nfl_teams import TEAM_DIVISIONS, is_real_franchise_matchup
from library.serving.nfl_reads import _home_and_away
from library.storage.feature_storage import FeatureStorage
from library.storage.season_projections import season_projection_key

logger = logging.getLogger("nfl-predict")

SPORT = "nfl"

# Source of truth is Terraform/scheduler-nfl-train-player-prop-model.tf's
# nfl_player_prop_stats map -- duplicated here as a plain list (not read
# from Terraform at runtime) since there's no DynamoDB-backed model
# registry yet (design/PROJECT_PLAN.md Phase 4) to read it from instead.
PLAYER_PROP_STATS = [
    "passing_yards", "passing_touchdowns", "rushing_yards", "rushing_touchdowns",
    "receiving_yards", "receiving_touchdowns", "defensive_sacks",
]


def _season_standings_inputs(storage: FeatureStorage) -> dict:
    """Fetches this season's completed+scheduled events once and derives
    everything season_simulation.simulate_season needs, plus each team's
    next scheduled event_key (reused by _leaderboards below)."""
    # Excludes the Pro Bowl and any other exhibition matchup -- its AFC/NFC
    # all-star "teams" aren't real franchises (see
    # library.features.nfl_teams.is_real_franchise_matchup), so a played
    # one would otherwise count as a real win/loss and Elo update for a
    # non-existent team_id.
    scheduled = [e for e in storage.get_all_events(SPORT, status="scheduled") if is_real_franchise_matchup(e)]
    all_completed = [e for e in storage.get_all_events(SPORT, status="completed") if is_real_franchise_matchup(e)]
    current_season = max(
        (e.get("season") for e in scheduled + all_completed if e.get("season") is not None), default=None,
    )
    scheduled = [e for e in scheduled if e.get("season") == current_season]
    # Wins/losses/point-differential are scoped to just this season --
    # standings reset every year regardless of Elo. compute_elo_ratings
    # below gets the FULL (unscoped) history instead, since it does its
    # own season-boundary regression (see that function's docstring).
    completed = [e for e in all_completed if e.get("season") == current_season]

    wins: dict[str, int] = {}
    losses: dict[str, int] = {}
    ties: dict[str, int] = {}
    point_differential: dict[str, int] = {}
    team_last_completed_date: dict[str, str] = {}
    for event in completed:
        home_away = _home_and_away(event)
        if home_away is None:
            continue
        for entity_id, opponent_id in (home_away, home_away[::-1]):
            participant = next(p for p in event["participants"] if p.get("entity_id") == entity_id)
            opponent = next(p for p in event["participants"] if p.get("entity_id") == opponent_id)
            score = (participant.get("result") or {}).get("score")
            opponent_score = (opponent.get("result") or {}).get("score")
            if score is None or opponent_score is None:
                continue
            wins[entity_id] = wins.get(entity_id, 0) + (1 if score > opponent_score else 0)
            losses[entity_id] = losses.get(entity_id, 0) + (1 if score < opponent_score else 0)
            ties[entity_id] = ties.get(entity_id, 0) + (1 if score == opponent_score else 0)
            point_differential[entity_id] = point_differential.get(entity_id, 0) + (score - opponent_score)
            event_date = event.get("event_date", "")
            if event_date > team_last_completed_date.get(entity_id, ""):
                team_last_completed_date[entity_id] = event_date

    _, current_ratings = compute_elo_ratings(all_completed, as_of_season=current_season)

    scheduled_sorted = sorted(scheduled, key=lambda e: e.get("event_date", ""))
    remaining_games = []
    team_next_event: dict[str, str] = {}
    for event in scheduled_sorted:
        home_away = _home_and_away(event)
        if home_away is None:
            continue
        home_id, away_id = home_away
        remaining_games.append((home_id, away_id))
        team_next_event.setdefault(home_id, event["event_key"])
        team_next_event.setdefault(away_id, event["event_key"])

    return {
        "current_season": current_season,
        "completed_event_keys": {e["event_key"] for e in completed},
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "point_differential": point_differential,
        "current_ratings": current_ratings,
        "remaining_games": remaining_games,
        "team_next_event": team_next_event,
        "team_last_completed_date": team_last_completed_date,
        "games_remaining": Counter(team_id for pair in remaining_games for team_id in pair),
    }


def _season_wide_candidate_rows(storage: FeatureStorage, season_inputs: dict) -> dict[str, list[dict]]:
    """Every candidate likely to lead at least one team in a tracked stat
    category (passing/receiving/rushing/sacks), sourced from each team's
    own next scheduled event's depth chart via
    live_features.build_live_event_leader_candidates -- run once per
    unique next event, not per team, since the two teams playing each
    other share one."""
    next_event_keys = {key for key in season_inputs["team_next_event"].values() if key}
    rows_by_category: dict[str, list[dict]] = {"passing": [], "receiving": [], "rushing": [], "sacks": []}

    def _candidates_for_event(event_key: str) -> dict | None:
        try:
            return live_features.build_live_event_leader_candidates(storage, SPORT, event_key)
        except Exception:
            logger.exception("Failed building season-wide candidates for %s", event_key)
            return None

    with ThreadPoolExecutor(max_workers=max(1, min(len(next_event_keys), 10))) as executor:
        for candidates in executor.map(_candidates_for_event, next_event_keys):
            if candidates is None:
                continue
            for side in ("home", "away"):
                team_candidates = candidates[side]
                if team_candidates["passing"]:
                    rows_by_category["passing"].append(team_candidates["passing"][0])
                rows_by_category["receiving"].extend(team_candidates["receiving"])
                rows_by_category["rushing"].extend(team_candidates["rushing"])
                rows_by_category["sacks"].extend(team_candidates["sacks"])

    return rows_by_category


def _leaderboards(storage: FeatureStorage, s3, model_cache: dict, season_inputs: dict) -> dict:
    """Top-10 season-long leaderboard per tracked player-prop stat,
    projected as current season-to-date total + (their own model's
    prediction for their team's NEXT scheduled game * games remaining) --
    see season_simulation.project_leaderboard's own docstring for why
    this is a flat estimate rather than a per-opponent simulation. With
    zero current-season total (before Week 1, or a candidate who simply
    hasn't recorded this particular stat yet), this reduces to a pure
    full-season projection, so the same formula covers both cases."""
    season_player_stats = [
        row for row in storage.get_all_player_game_stats(SPORT)
        if row.get("event_key") in season_inputs["completed_event_keys"]
    ]

    player_team: dict[str, str] = {}
    for row in season_player_stats:
        player_team.setdefault(row["entity_id"], row.get("team_id"))

    current_totals_by_stat: dict[str, dict[str, float]] = {stat: {} for stat in PLAYER_PROP_STATS}
    for row in season_player_stats:
        entity_id = row["entity_id"]
        stat_line = row.get("stat_line", {})
        for stat in PLAYER_PROP_STATS:
            value = stat_line.get(stat)
            if value is not None:
                totals = current_totals_by_stat[stat]
                totals[entity_id] = totals.get(entity_id, 0) + value

    # feature_row_cache pre-populated from the depth-chart-sourced rows --
    # build_live_event_leader_candidates already returns a full live
    # feature row per candidate, so those never need a separate
    # build_live_player_features call the way a this-season-stats-only
    # candidate below still does.
    feature_row_cache: dict[str, dict] = {}
    stat_candidates: dict[str, set[str]] = {stat: set(current_totals_by_stat[stat]) for stat in PLAYER_PROP_STATS}
    for category, rows in _season_wide_candidate_rows(storage, season_inputs).items():
        for row in rows:
            entity_id = row["entity_id"]
            feature_row_cache[entity_id] = row
            player_team.setdefault(entity_id, row.get("team_id"))
            for stat in event_prediction.LEADER_CATEGORY_STATS[category]:
                stat_candidates[stat].add(entity_id)

    # Any candidate not already covered above (this-season stats exist,
    # but they weren't in their team's depth-chart snapshot -- a backup
    # who got real volume, say) still needs its own feature row built.
    all_candidates = {entity_id for entity_ids in stat_candidates.values() for entity_id in entity_ids}
    remaining = all_candidates - feature_row_cache.keys()

    def _build_row(entity_id: str) -> tuple[str, dict | None]:
        next_event_key = season_inputs["team_next_event"].get(player_team.get(entity_id))
        if next_event_key is None:
            return entity_id, None
        try:
            feature_row = live_features.build_live_player_features(
                storage, SPORT, next_event_key, entity_id,
                current_ratings=season_inputs["current_ratings"],
                team_last_event_dates=season_inputs["team_last_completed_date"],
            )
            return entity_id, feature_row
        except live_features.EventNotFoundError:
            return entity_id, None
        except Exception:
            logger.exception("Failed to build live features for %s", entity_id)
            return entity_id, None

    with ThreadPoolExecutor(max_workers=max(1, min(len(remaining), 10))) as executor:
        for entity_id, feature_row in executor.map(_build_row, remaining):
            if feature_row is not None:
                feature_row_cache[entity_id] = feature_row

    leaderboards: dict[str, list[dict]] = {}
    for stat in PLAYER_PROP_STATS:
        candidates = stat_candidates[stat]
        current_totals = {entity_id: current_totals_by_stat[stat].get(entity_id, 0.0) for entity_id in candidates}
        model_name = event_prediction.model_name_to_prop(stat)
        try:
            booster, model_card = event_prediction.get_cached_model(model_cache, s3, model_name)
        except model_loader.NoPromotedModelError:
            booster = None

        per_game_projections: dict[str, float] = {}
        if booster is not None:
            for entity_id in candidates:
                feature_row = feature_row_cache.get(entity_id)
                if feature_row is not None:
                    prediction = model_loader.predict(booster, model_card, feature_row)
                    per_game_projections[entity_id] = event_prediction.non_negative(prediction)

        games_remaining = {
            entity_id: season_inputs["games_remaining"].get(player_team.get(entity_id), 0)
            for entity_id in candidates
        }

        top = season_simulation.project_leaderboard(current_totals, per_game_projections, games_remaining, top_n=10)
        for row in top:
            entity = storage.get_entity(SPORT, row["entity_id"])
            if entity and entity.get("name"):
                row["name"] = entity["name"]
        leaderboards[stat] = top

    return leaderboards


def build_season_projection(storage: FeatureStorage, s3) -> dict:
    model_cache: dict = {}

    season_inputs = _season_standings_inputs(storage)
    simulation = season_simulation.simulate_season(
        season_inputs["wins"], season_inputs["losses"], season_inputs["point_differential"],
        season_inputs["remaining_games"], season_inputs["current_ratings"],
    )

    # division lets the frontend group standings by division (see
    # season_page.dart) without duplicating TEAM_DIVISIONS client-side.
    # Sorted by projected_wins descending BEFORE grouping, so each
    # division's own teams are already best-to-worst within their group.
    standings = sorted(
        (
            {
                "team_id": team_id,
                "division": TEAM_DIVISIONS.get(team_id),
                "wins": season_inputs["wins"].get(team_id, 0),
                "losses": season_inputs["losses"].get(team_id, 0),
                "ties": season_inputs["ties"].get(team_id, 0),
                **projection,
            }
            for team_id, projection in simulation.items()
        ),
        key=lambda row: row["projected_wins"],
        reverse=True,
    )

    try:
        leaderboards = _leaderboards(storage, s3, model_cache, season_inputs)
    except Exception:
        logger.exception("Failed to build season leaderboards")
        leaderboards = None

    return {
        "sport": SPORT,
        "season": season_inputs["current_season"],
        "standings": standings,
        "leaderboards": leaderboards,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def run_scheduled(storage: FeatureStorage, model_bucket) -> dict:
    """Entry point for Terraform/scheduler-nfl-season-projection.tf's
    weekly EventBridge Scheduler -> Lambda direct invoke -- computes
    build_season_projection() once and writes it to S3 instead of
    returning it through API Gateway. Not wrapped in the same try/except
    handler.py's API-Gateway-triggered routes use: there's no HTTP caller
    waiting on a status code here, so a real failure should propagate and
    show up as a Lambda error/CloudWatch alarm, not get silently reshaped
    into a 500 nobody reads."""
    result = build_season_projection(storage, model_bucket)
    model_bucket.put_json(season_projection_key(SPORT), result)
    logger.info("Wrote season projection for %s to S3", SPORT)
    return {"status": "ok"}
