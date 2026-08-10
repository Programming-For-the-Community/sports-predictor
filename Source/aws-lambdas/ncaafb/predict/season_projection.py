"""
Season-long standings/leaderboard orchestration -- builds the payload
Terraform/scheduler-ncaafb-season-projection.tf's weekly EventBridge
Scheduler invoke writes to S3 for GET /ncaafb/season (see handler.py's
own docstring for why that route can't compute this live per-request,
same 26-29s-against-API-Gateway's-29s-ceiling reasoning as NFL's own
season_projection.py). Mirrors that module's own role and shape, but NOT
a port of it -- see season_simulation.py's own docstring for the biggest
difference (CFP field selection needs the real trained national-ranking
model, not just Elo).

Pulls this season's data once via FeatureStorage, derives Elo ratings,
each team's real-time conference/record/scoring/strength-of-schedule
snapshot, and the remaining schedule (_season_standings_inputs), runs
season_simulation's pure Monte Carlo logic with a ranking-model-backed
score_teams callable (_batch_score_teams), and scores each tracked
player-prop leaderboard (_leaderboards) using the same model-loading
helpers event_prediction.py uses for a single live request.
"""
import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pandas as pd

import event_prediction
import live_features
import model_loader
import season_simulation
from library.features.common import compute_elo_ratings, current_streak, rolling_team_scoring_averages
from library.features.ncaafb import average_opponent_elo
from library.ml.model_types import ADAPTERS
from library.serving.ncaafb_reads import _home_and_away
from library.storage.feature_storage import FeatureStorage
from library.storage.season_projections import season_projection_key

logger = logging.getLogger("ncaafb-predict")

SPORT = "ncaafb"
RANKING_MODEL_NAME = "national-ranking"

# Source of truth is Terraform/dynamodb-sport-registry.tf's own
# ncaafb_player_prop_stats map -- duplicated as a plain list, same
# reasoning nfl/predict/season_projection.py's own PLAYER_PROP_STATS
# comment gives (no DynamoDB-backed model registry yet to read it from
# instead).
PLAYER_PROP_STATS = [
    "passing_yards", "passing_touchdowns", "rushing_yards", "rushing_touchdowns",
    "receiving_yards", "receiving_touchdowns", "defensive_sacks",
]


def _season_standings_inputs(storage: FeatureStorage) -> dict:
    """Fetches this season's completed+scheduled events once and derives
    everything season_simulation.simulate_season (and this season's
    National Ranking feature rows) need.

    team_conference comes straight off each event's own home_conference/
    away_conference (CFBD's season-scoped fields, see
    library/normalize/ncaafb.py's game_to_event_item), not a static
    table -- see library/features/ncaafb.py's own docstring for why no
    such table exists. remaining_games only keeps a pairing when BOTH
    sides have a known conference this season -- an FBS team's rare FCS
    buy game has no meaningful conference/Elo rating for the FCS side to
    simulate against, so it's excluded from the walk-forward loop
    entirely (a documented undercount of a small number of "should-win"
    non-conference games, not a crash risk -- see
    season_simulation.simulate_season's own docstring for the second,
    defensive guard against the same gap).
    """
    scheduled = storage.get_all_events(SPORT, status="scheduled")
    all_completed = storage.get_all_events(SPORT, status="completed")
    current_season = max(
        (e.get("season") for e in scheduled + all_completed if e.get("season") is not None), default=None,
    )
    scheduled = [e for e in scheduled if e.get("season") == current_season]
    completed = [e for e in all_completed if e.get("season") == current_season]

    wins: dict[str, int] = {}
    losses: dict[str, int] = {}
    ties: dict[str, int] = {}
    point_differential: dict[str, int] = {}
    team_last_completed_date: dict[str, str] = {}
    team_conference: dict[str, str] = {}
    completed_by_team: dict[str, list[dict]] = {}
    for event in completed:
        home_away = _home_and_away(event)
        if home_away is None:
            continue
        home_id, away_id = home_away
        if event.get("home_conference"):
            team_conference[home_id] = event["home_conference"]
        if event.get("away_conference"):
            team_conference[away_id] = event["away_conference"]
        completed_by_team.setdefault(home_id, []).append(event)
        completed_by_team.setdefault(away_id, []).append(event)

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

    for team_id, team_events in completed_by_team.items():
        team_events.sort(key=lambda e: e.get("event_date", ""), reverse=True)

    pre_game_ratings, current_ratings = compute_elo_ratings(all_completed, as_of_season=current_season)

    avg_points_scored: dict[str, float | None] = {}
    avg_points_allowed: dict[str, float | None] = {}
    win_streak: dict[str, int] = {}
    strength_of_schedule: dict[str, float | None] = {}
    for team_id, team_events in completed_by_team.items():
        scoring = rolling_team_scoring_averages(team_events, team_id, window=len(team_events))
        avg_points_scored[team_id] = scoring["avg_points_scored"]
        avg_points_allowed[team_id] = scoring["avg_points_allowed"]
        win_streak[team_id] = current_streak(team_events, team_id)
        strength_of_schedule[team_id] = average_opponent_elo(team_events, team_id, pre_game_ratings)

    scheduled_sorted = sorted(scheduled, key=lambda e: e.get("event_date", ""))
    remaining_games = []
    team_next_event: dict[str, str] = {}
    for event in scheduled_sorted:
        home_away = _home_and_away(event)
        if home_away is None:
            continue
        home_id, away_id = home_away
        if event.get("home_conference"):
            team_conference.setdefault(home_id, event["home_conference"])
        if event.get("away_conference"):
            team_conference.setdefault(away_id, event["away_conference"])
        if home_id in team_conference and away_id in team_conference:
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
        "team_conference": team_conference,
        "remaining_games": remaining_games,
        "team_next_event": team_next_event,
        "team_last_completed_date": team_last_completed_date,
        "games_remaining": Counter(team_id for pair in remaining_games for team_id in pair),
        "avg_points_scored": avg_points_scored,
        "avg_points_allowed": avg_points_allowed,
        "win_streak": win_streak,
        "strength_of_schedule": strength_of_schedule,
    }


def _ranking_feature_row(team_id: str, wins: dict, losses: dict, ratings: dict, season_inputs: dict) -> dict:
    """One synthetic team-week row for the National Ranking model,
    matching build_team_week_features' own column set (Source/library/
    features/ncaafb.py) -- wins/losses/elo/games_played are THIS Monte
    Carlo iteration's simulated end-of-season values; avg_points_scored/
    allowed, win_streak, and strength_of_schedule are held at today's
    real season-to-date value for every iteration instead of being
    re-simulated (simulate_season never generates real scores to derive
    them from -- see that module's own docstring for the identical
    "no margin of victory" simplification NFL's own module documents).
    A team with zero completed games this season (e.g. a fresh
    preseason projection) gets None/0 for all of these, same as a
    genuinely-unranked team-week at training time -- model_loader.
    predict already coerces a missing feature to NaN."""
    games_played = wins.get(team_id, 0) + losses.get(team_id, 0)
    return {
        "elo": ratings.get(team_id),
        "wins": wins.get(team_id, 0),
        "losses": losses.get(team_id, 0),
        "games_played": games_played,
        "avg_points_scored": season_inputs["avg_points_scored"].get(team_id),
        "avg_points_allowed": season_inputs["avg_points_allowed"].get(team_id),
        "win_streak": season_inputs["win_streak"].get(team_id, 0),
        "strength_of_schedule": season_inputs["strength_of_schedule"].get(team_id),
        "week": season_inputs["current_season"],
    }


def _batch_score_teams(estimator, model_card: dict, teams: list[str], season_inputs: dict, wins: dict, losses: dict, ratings: dict) -> dict[str, float]:
    """Scores every team in ONE batched adapter.predict call -- see
    season_simulation.py's own docstring for why that's what makes
    calling the real trained model inside a season_simulation.
    simulate_season Monte Carlo loop tractable at all (simulations x one
    batched call, not simulations x len(teams) individual calls).
    Bypasses model_loader.predict's own single-row wrapper for exactly
    this reason -- same NaN-for-missing coercion, just vectorized."""
    feature_columns = model_card["feature_columns"]
    rows = []
    for team_id in teams:
        row = _ranking_feature_row(team_id, wins, losses, ratings, season_inputs)
        rows.append({
            column: float(row[column]) if isinstance(row.get(column), (int, float)) else float("nan")
            for column in feature_columns
        })
    X = pd.DataFrame(rows, columns=feature_columns, index=teams)
    adapter = ADAPTERS[model_card["algorithm"]]
    predictions = adapter.predict(estimator, X)
    return dict(zip(teams, (float(value) for value in predictions)))


def _leaderboards(storage: FeatureStorage, s3, model_cache: dict, season_inputs: dict) -> dict:
    """Top-10 season-long leaderboard per tracked player-prop stat,
    projected as current season-to-date total + (their own model's
    prediction for their team's NEXT scheduled game * games remaining) --
    see season_simulation.project_leaderboard's own docstring for why
    this is a flat estimate. Candidates are simply every player who has
    recorded at least one value for that stat this season -- unlike NFL's
    own _leaderboards, there's no depth-chart candidate-widening step
    here at all (NCAAFB has no depth chart to widen from -- see
    live_features.py's own docstring), so this is simpler by
    construction, not a simplification of something NFL has that this
    is missing."""
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

    all_candidates = {entity_id for totals in current_totals_by_stat.values() for entity_id in totals}

    def _build_row(entity_id: str) -> tuple[str, dict | None]:
        next_event_key = season_inputs["team_next_event"].get(player_team.get(entity_id))
        if next_event_key is None:
            return entity_id, None
        try:
            feature_row = live_features.build_live_player_features(
                storage, SPORT, next_event_key, entity_id, current_ratings=season_inputs["current_ratings"],
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
        candidates = set(current_totals_by_stat[stat])
        current_totals = current_totals_by_stat[stat]
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
    season_inputs = _season_standings_inputs(storage)
    teams = list(season_inputs["team_conference"])

    simulation: dict[str, dict] = {}
    if len(teams) >= season_simulation.CFP_FIELD_SIZE:
        try:
            estimator, model_card = model_loader.load_current_model(s3, SPORT, RANKING_MODEL_NAME)

            def score_teams(wins: dict, losses: dict, ratings: dict) -> dict[str, float]:
                return _batch_score_teams(estimator, model_card, teams, season_inputs, wins, losses, ratings)

            simulation = season_simulation.simulate_season(
                season_inputs["wins"], season_inputs["losses"], season_inputs["point_differential"],
                season_inputs["remaining_games"], season_inputs["current_ratings"],
                season_inputs["team_conference"], score_teams,
            )
        except model_loader.NoPromotedModelError:
            # No promoted ranking model yet -- standings still show real
            # record/conference, just without projected_wins/bowl/CFP/
            # championship probabilities. Same "best-effort, never fail
            # the whole projection over one missing model" spirit as
            # _leaderboards' own try/except below.
            logger.warning("No promoted %s model -- season simulation skipped this run", RANKING_MODEL_NAME)

    standings = sorted(
        (
            {
                "team_id": team_id,
                "conference": season_inputs["team_conference"].get(team_id),
                "wins": season_inputs["wins"].get(team_id, 0),
                "losses": season_inputs["losses"].get(team_id, 0),
                "ties": season_inputs["ties"].get(team_id, 0),
                **simulation.get(team_id, {}),
            }
            for team_id in teams
        ),
        key=lambda row: row.get("projected_wins", row["wins"]),
        reverse=True,
    )

    try:
        leaderboards = _leaderboards(storage, s3, {}, season_inputs)
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
    """Entry point for Terraform/scheduler-ncaafb-season-projection.tf's
    weekly EventBridge Scheduler -> Lambda direct invoke -- computes
    build_season_projection() once and writes it to S3 instead of
    returning it through API Gateway. Not wrapped in the same try/except
    handler.py's API-Gateway-triggered routes use -- see NFL's own
    season_projection.py's identical docstring for why."""
    result = build_season_projection(storage, model_bucket)
    model_bucket.put_json(season_projection_key(SPORT), result)
    logger.info("Wrote season projection for %s to S3", SPORT)
    return {"status": "ok"}
