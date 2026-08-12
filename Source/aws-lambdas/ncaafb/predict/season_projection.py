"""
Builds the season projection (standings, bowl/CFP/championship
probabilities) that Terraform/scheduler-ncaafb-season-projection.tf's
weekly EventBridge Scheduler invoke writes to S3 for GET /ncaafb/season.
Team outcomes only -- no player-prop leaderboard here, unlike NFL's own
season_projection.py (NCAAFB's season simulation was deliberately scoped
to team outcomes, see design/PROJECT_PLAN.md's NCAAFB section).

Pulls this season's data once via FeatureStorage, derives Elo ratings and
each team's conference/record/scoring/strength-of-schedule snapshot
(_season_standings_inputs), and runs season_simulation's Monte Carlo
logic with a ranking-model-backed score_teams callable
(_batch_score_teams).
"""
import logging
from collections import Counter
from datetime import datetime, timezone

import pandas as pd

import model_loader
import season_simulation
from library.features.common import compute_elo_ratings, current_streak, rolling_team_scoring_averages
from library.features.ncaafb import average_opponent_elo
from library.ml.model_types import ADAPTERS
from library.serving.common import enrich_team_standings
from library.serving.ncaafb_reads import _home_and_away
from library.storage.feature_storage import FeatureStorage
from library.storage.season_projections import season_projection_key

logger = logging.getLogger("ncaafb-predict")

SPORT = "ncaafb"
RANKING_MODEL_NAME = "national-ranking"


def _season_standings_inputs(storage: FeatureStorage) -> dict:
    """Fetches this season's completed+scheduled events once and derives
    everything simulate_season and the ranking feature rows need.
    team_conference comes from each event's own home_conference/
    away_conference. remaining_games only keeps a pairing when both sides
    have a known conference (excludes FCS buy games)."""
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
    """One team-week row for the ranking model, matching
    build_team_week_features' column set. wins/losses/elo/games_played
    are this Monte Carlo iteration's simulated values; avg_points_scored/
    allowed, win_streak, and strength_of_schedule stay at today's real
    season-to-date value (simulate_season never generates real scores to
    derive them from)."""
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
    """Scores every team in one batched adapter.predict call (not one call per team).
    Bypasses model_loader.predict's single-row wrapper; same NaN-for-missing coercion, vectorized."""
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
    standings = enrich_team_standings(storage, SPORT, standings)

    return {
        "sport": SPORT,
        "season": season_inputs["current_season"],
        "standings": standings,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def run_scheduled(storage: FeatureStorage, model_bucket) -> dict:
    """Entry point for the weekly EventBridge Scheduler invoke -- computes the projection
    once and writes it to S3."""
    result = build_season_projection(storage, model_bucket)
    model_bucket.put_json(season_projection_key(SPORT), result)
    logger.info("Wrote season projection for %s to S3", SPORT)
    return {"status": "ok"}
