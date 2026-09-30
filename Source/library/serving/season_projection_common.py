"""
Season-projection pieces sports compute the same way: this season's events,
season-to-date player-prop totals and one player-prop stat's projected
top-10 leaderboard (NFL/NBA); per-team rolling stats and batched
ranking-model scoring (NCAAFB/NCAA MBB).
"""
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from types import ModuleType

import pandas as pd

from library.features.common import average_opponent_elo, current_streak, rolling_team_scoring_averages
from library.ml.model_types import ADAPTERS
from library.serving import model_loader


def current_season_events(
    storage, sport: str, include: Callable[[dict], bool],
) -> tuple[list[dict], list[dict], list[dict], int | None]:
    """(scheduled, all_completed, completed, current_season) -- scheduled/
    completed are scoped to current_season; all_completed is the full
    history compute_elo_ratings needs for its season-boundary regression.
    `include` drops any event that isn't a real game between two real
    franchises (all-star and exhibition matchups)."""
    scheduled = [e for e in storage.get_all_events(sport, status="scheduled") if include(e)]
    all_completed = [e for e in storage.get_all_events(sport, status="completed") if include(e)]
    current_season = max(
        (e.get("season") for e in scheduled + all_completed if e.get("season") is not None), default=None,
    )
    scheduled = [e for e in scheduled if e.get("season") == current_season]
    completed = [e for e in all_completed if e.get("season") == current_season]
    return scheduled, all_completed, completed, current_season


def current_season_totals(
    season_player_stats: list[dict], stats: list[str],
) -> tuple[dict[str, str], dict[str, dict[str, float]]]:
    """(player_team, current_totals_by_stat) from this season's completed-game stat lines."""
    player_team: dict[str, str] = {}
    for row in season_player_stats:
        player_team.setdefault(row["entity_id"], row.get("team_id"))

    current_totals_by_stat: dict[str, dict[str, float]] = {stat: {} for stat in stats}
    for row in season_player_stats:
        entity_id = row["entity_id"]
        stat_line = row.get("stat_line", {})
        for stat in stats:
            value = stat_line.get(stat)
            if value is not None:
                totals = current_totals_by_stat[stat]
                totals[entity_id] = totals.get(entity_id, 0) + value
    return player_team, current_totals_by_stat


def project_stat_leaderboard(
    storage, sport: str, s3, model_cache: dict, season_inputs: dict, stat: str, candidates: set[str],
    current_totals_by_stat: dict[str, dict[str, float]], feature_row_cache: dict[str, dict],
    player_team: dict[str, str], *, event_prediction: ModuleType, project_leaderboard: Callable[..., list[dict]],
) -> list[dict]:
    """Top-10 leaderboard for one player-prop stat -- season-to-date total
    plus each candidate's own model prediction for their team's next game
    times games remaining. With no season-to-date total (before the first
    game, or a candidate who hasn't recorded this stat yet), this reduces to
    a pure full-season projection. `event_prediction` is the sport's own
    module (model lookup and naming); `project_leaderboard` is its
    season_simulation.project_leaderboard."""
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

    top = project_leaderboard(current_totals, per_game_projections, games_remaining, top_n=10)
    for row in top:
        entity = storage.get_entity(sport, row["entity_id"], "player")
        if entity and entity.get("name"):
            row["name"] = entity["name"]
    return top


def record_game_result_with_ties(
    event: dict, entity_id: str, opponent_id: str,
    wins: dict[str, int], losses: dict[str, int], ties: dict[str, int], point_differential: dict[str, int],
    team_last_completed_date: dict[str, str],
) -> None:
    """Credits entity_id's own side of one completed game into
    wins/losses/ties/point_differential and updates
    team_last_completed_date -- no-op if either side's own score is
    missing."""
    participant = next(p for p in event["participants"] if p.get("entity_id") == entity_id)
    opponent = next(p for p in event["participants"] if p.get("entity_id") == opponent_id)
    score = (participant.get("result") or {}).get("score")
    opponent_score = (opponent.get("result") or {}).get("score")
    if score is None or opponent_score is None:
        return
    wins[entity_id] = wins.get(entity_id, 0) + (1 if score > opponent_score else 0)
    losses[entity_id] = losses.get(entity_id, 0) + (1 if score < opponent_score else 0)
    ties[entity_id] = ties.get(entity_id, 0) + (1 if score == opponent_score else 0)
    point_differential[entity_id] = point_differential.get(entity_id, 0) + (score - opponent_score)
    event_date = event.get("event_date", "")
    if event_date > team_last_completed_date.get(entity_id, ""):
        team_last_completed_date[entity_id] = event_date


def season_wide_feature_rows(
    candidate_rows: dict[str, list[dict]], stats: list[str], leader_category_stats: dict[str, list[str]],
    current_totals_by_stat: dict[str, dict[str, float]], player_team: dict[str, str],
) -> tuple[dict[str, dict], dict[str, set[str]]]:
    """(feature_row_cache, stat_candidates) pre-populated from
    candidate_rows ({leader category: [live feature row, ...]}). Mutates
    player_team in place with any candidate not already covered by this
    season's own stat lines."""
    feature_row_cache: dict[str, dict] = {}
    stat_candidates: dict[str, set[str]] = {stat: set(current_totals_by_stat[stat]) for stat in stats}
    for category, rows in candidate_rows.items():
        for row in rows:
            entity_id = row["entity_id"]
            feature_row_cache[entity_id] = row
            player_team.setdefault(entity_id, row.get("team_id"))
            for stat in leader_category_stats[category]:
                stat_candidates[stat].add(entity_id)
    return feature_row_cache, stat_candidates


def fill_remaining_feature_rows(
    season_inputs: dict, player_team: dict[str, str], feature_row_cache: dict[str, dict], remaining: set[str],
    build_row: Callable[[str, str], dict], event_not_found: type[Exception], logger,
) -> None:
    """Builds a live feature row -- build_row(next_event_key, entity_id)
    -- for every remaining candidate whose team has a next event, in
    parallel; mutates feature_row_cache in place. A candidate whose row
    fails to build is left out."""
    def _build_row(entity_id: str) -> tuple[str, dict | None]:
        next_event_key = season_inputs["team_next_event"].get(player_team.get(entity_id))
        if next_event_key is None:
            return entity_id, None
        try:
            return entity_id, build_row(next_event_key, entity_id)
        except event_not_found:
            return entity_id, None
        except Exception:
            logger.exception("Failed to build live features for %s", entity_id)
            return entity_id, None

    with ThreadPoolExecutor(max_workers=max(1, min(len(remaining), 10))) as executor:
        for entity_id, feature_row in executor.map(_build_row, remaining):
            if feature_row is not None:
                feature_row_cache[entity_id] = feature_row


def team_rolling_stats(
    completed_by_team: dict[str, list[dict]], pre_game_ratings: dict[str, float],
) -> tuple[dict, dict, dict, dict]:
    """(avg_points_scored, avg_points_allowed, win_streak,
    strength_of_schedule), one entry per team with >=1 completed game."""
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
    return avg_points_scored, avg_points_allowed, win_streak, strength_of_schedule


def score_rows(estimator, model_card: dict, rows_by_team: dict[str, dict]) -> dict[str, float]:
    """Scores every team's feature row in one batched adapter.predict call,
    with model_loader.predict's NaN-for-missing coercion, vectorized."""
    feature_columns = model_card["feature_columns"]
    teams = list(rows_by_team)
    rows = [
        {
            column: float(row[column]) if isinstance(row.get(column), (int, float)) else float("nan")
            for column in feature_columns
        }
        for row in rows_by_team.values()
    ]
    X = pd.DataFrame(rows, columns=feature_columns, index=teams)
    predictions = ADAPTERS[model_card["algorithm"]].predict(estimator, X)
    return dict(zip(teams, (float(value) for value in predictions)))


def rank_by_score(teams: list[str], scores: dict[str, float]) -> dict[str, int]:
    """1-based rank per team, lowest score first."""
    ranked = sorted(teams, key=lambda team_id: scores[team_id])
    return {team_id: rank for rank, team_id in enumerate(ranked, start=1)}
