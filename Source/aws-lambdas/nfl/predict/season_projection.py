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
from library.features.nfl_teams import is_real_franchise_matchup
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
    completed = [e for e in storage.get_all_events(SPORT, status="completed") if is_real_franchise_matchup(e)]
    current_season = max(
        (e.get("season") for e in scheduled + completed if e.get("season") is not None), default=None,
    )
    # Scoping `completed` to just this season before it reaches
    # compute_elo_ratings below is what resets every team to
    # DEFAULT_STARTING_RATING at the start of each season, ignoring how the
    # previous one ended -- intentional, not incidental: a brand-new season
    # with no completed games yet passes an empty list in, so
    # compute_elo_ratings returns empty ratings and simulate_season's own
    # `ratings.get(team_id, DEFAULT_STARTING_RATING)` fallback applies to
    # every team equally.
    scheduled = [e for e in scheduled if e.get("season") == current_season]
    completed = [e for e in completed if e.get("season") == current_season]

    wins: dict[str, int] = {}
    losses: dict[str, int] = {}
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
            point_differential[entity_id] = point_differential.get(entity_id, 0) + (score - opponent_score)
            event_date = event.get("event_date", "")
            if event_date > team_last_completed_date.get(entity_id, ""):
                team_last_completed_date[entity_id] = event_date

    _, current_ratings = compute_elo_ratings(completed)

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
        "point_differential": point_differential,
        "current_ratings": current_ratings,
        "remaining_games": remaining_games,
        "team_next_event": team_next_event,
        "team_last_completed_date": team_last_completed_date,
        "games_remaining": Counter(team_id for pair in remaining_games for team_id in pair),
    }


def _leaderboards(storage: FeatureStorage, s3, model_cache: dict, season_inputs: dict) -> dict:
    """Top-10 season-long leaderboard per tracked player-prop stat,
    projected as current season-to-date total + (their own model's
    prediction for their team's NEXT scheduled game * games remaining) --
    see season_simulation.project_leaderboard's own docstring for why
    this is a flat estimate rather than a per-opponent simulation."""
    season_player_stats = [
        row for row in storage.get_all_player_game_stats(SPORT)
        if row.get("event_key") in season_inputs["completed_event_keys"]
    ]

    player_team: dict[str, str] = {}
    for row in season_player_stats:
        player_team.setdefault(row["entity_id"], row.get("team_id"))

    # Current-season totals for every stat, computed first and entirely
    # from season_player_stats (already fetched once above) -- no storage
    # calls here, so the full cross-stat candidate set is known before any
    # live feature row gets built below.
    current_totals_by_stat: dict[str, dict[str, float]] = {stat: {} for stat in PLAYER_PROP_STATS}
    for row in season_player_stats:
        entity_id = row["entity_id"]
        stat_line = row.get("stat_line", {})
        for stat in PLAYER_PROP_STATS:
            value = stat_line.get(stat)
            if value is not None:
                totals = current_totals_by_stat[stat]
                totals[entity_id] = totals.get(entity_id, 0) + value

    # A candidate who records more than one tracked stat (nearly every real
    # QB shows up in both passing_yards and passing_touchdowns; a
    # dual-threat QB or receiving RB shows up across even more) still needs
    # only one build_live_player_features call: it returns every stat
    # category's rolling averages in one row regardless of which model
    # later reads it. Building this UNION of candidates once, up front, and
    # reusing the same row for every stat's projection avoids re-fetching
    # get_event/get_entity/get_player_game_stats once per stat for the same
    # repeat players, on top of what current_ratings/team_last_event_dates
    # below already save (those two, reused from _season_standings_inputs,
    # avoid a separate full-history Elo recompute per candidate).
    all_candidates = {entity_id for totals in current_totals_by_stat.values() for entity_id in totals}

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

    feature_row_cache: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(len(all_candidates), 10))) as executor:
        for entity_id, feature_row in executor.map(_build_row, all_candidates):
            if feature_row is not None:
                feature_row_cache[entity_id] = feature_row

    leaderboards: dict[str, list[dict]] = {}
    for stat in PLAYER_PROP_STATS:
        current_totals = current_totals_by_stat[stat]
        model_name = event_prediction.model_name_to_prop(stat)
        try:
            booster, model_card = event_prediction.get_cached_model(model_cache, s3, model_name)
        except model_loader.NoPromotedModelError:
            booster = None

        per_game_projections: dict[str, float] = {}
        if booster is not None:
            for entity_id in current_totals:
                feature_row = feature_row_cache.get(entity_id)
                if feature_row is not None:
                    per_game_projections[entity_id] = model_loader.predict(booster, model_card, feature_row)

        games_remaining = {
            entity_id: season_inputs["games_remaining"].get(player_team.get(entity_id), 0)
            for entity_id in current_totals
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

    standings = sorted(
        (
            {
                "team_id": team_id,
                "wins": season_inputs["wins"].get(team_id, 0),
                "losses": season_inputs["losses"].get(team_id, 0),
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
