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
from boto3.dynamodb.conditions import Key

import event_prediction
import model_loader
import season_simulation
from library.features.common import compute_elo_ratings, current_streak, rolling_team_scoring_averages
from library.features.ncaafb import average_opponent_elo
from library.ml.model_types import ADAPTERS
from library.serving.common import enrich_bracket_team_names, enrich_team_standings
from library.serving.ncaafb_reads import WIN_PROBABILITY_MODEL, _actual_result, _home_and_away
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


def _current_model_scores(estimator, model_card: dict, teams: list[str], season_inputs: dict) -> dict[str, float]:
    """Today's actual (not simulated) ranking-model score per team --
    the SAME model/feature row simulate_season's own score_teams callable
    uses to pick each simulated season's CFP field, scored once against
    real current wins/losses/ratings instead of a simulated future. Lower
    is better (see _batch_score_teams). Used both by _current_rankings
    (below, for the standings table's current_rank column) and by
    _bracket_payload (for real bracket seeding -- see that function's own
    docstring for why "today's real ranking" is used for seeding
    regardless of how much season remains, rather than trying to project
    a future ranking-model score forward)."""
    return _batch_score_teams(
        estimator, model_card, teams, season_inputs,
        season_inputs["wins"], season_inputs["losses"], season_inputs["current_ratings"],
    )


def _current_rankings(estimator, model_card: dict, teams: list[str], season_inputs: dict) -> dict[str, int]:
    """Today's actual National Ranking per team -- rank is just each
    team's 1-based position once sorted by _current_model_scores, not
    limited to the top 25, standings itself displays whatever's
    meaningful per row."""
    scores = _current_model_scores(estimator, model_card, teams, season_inputs)
    ranked = sorted(teams, key=lambda team_id: scores[team_id])
    return {team_id: rank for rank, team_id in enumerate(ranked, start=1)}


def _real_postseason_matchups(storage: FeatureStorage, current_season: int | None) -> dict[frozenset, dict]:
    """{frozenset({home_id, away_id}): event} for every real CFP game
    (is_playoff_game -- CFBD's own `playoff` field, confirmed live to be
    populated only for real 12-team CFP games, not every bowl -- see
    library/normalize/ncaafb.py's own docstring) this season, scheduled
    or completed."""
    result: dict[frozenset, dict] = {}
    for status in ("scheduled", "completed"):
        for event in storage.get_all_events(SPORT, status=status):
            if event.get("season") != current_season or not event.get("is_playoff_game"):
                continue
            home_away = _home_and_away(event)
            if home_away is None:
                continue
            result[frozenset(home_away)] = event
    return result


def _logged_win_probability(predictions_table, event_key_value: str) -> dict | None:
    """This event's own logged win-probability prediction, or None if
    nobody's ever requested one -- same "read the audit trail, never
    recompute" rule library/serving/ncaafb_reads.py's own
    _prediction_comparison follows."""
    rows = predictions_table.query(Key("event_key").eq(event_key_value))
    row = next((r for r in rows if r["model_key"].startswith(f"MODEL#{WIN_PROBABILITY_MODEL}#")), None)
    return row["predicted_value"] if row else None


def _resolve_matchup(
    team_a: str, team_b: str, seed_a: int | None, seed_b: int | None,
    real_matchups: dict[frozenset, dict], storage: FeatureStorage, s3, predictions_table,
    current_ratings: dict[str, float], home_advantage: float,
) -> dict:
    """Resolves one bracket slot -- the same 3-state design as NFL's own
    season_projection.py: (1) no real CFP game exists yet -- the model's
    own deterministic pick ("status": "projected"); (2) a real game
    exists and is completed -- the actual result plus whatever was
    originally predicted, if anyone ever requested one ("status":
    "final"); (3) a real game exists, not yet played -- computed on the
    spot right here if nobody's viewed it yet ("status": "scheduled")."""
    real_event = real_matchups.get(frozenset((team_a, team_b)))
    if real_event is None:
        matchup = season_simulation.project_matchup(team_a, team_b, seed_a, seed_b, current_ratings, home_advantage)
        matchup["status"] = "projected"
        return matchup

    event_key_value = real_event["event_key"]
    home_id, away_id = _home_and_away(real_event)

    if real_event.get("status") == "completed":
        actual = _actual_result(real_event)
        logged = _logged_win_probability(predictions_table, event_key_value)
        predicted_winner = win_probability = None
        if logged is not None:
            probability = logged["home_win_probability"]
            predicted_winner = home_id if probability >= 0.5 else away_id
            win_probability = probability if predicted_winner == home_id else 1 - probability
        return {
            "status": "final",
            "team_a": home_id, "team_b": away_id, "seed_a": seed_a, "seed_b": seed_b,
            "predicted_winner": predicted_winner, "win_probability": win_probability,
            "actual_winner": home_id if actual["home_won"] else away_id,
            "actual_home_score": actual["home_score"], "actual_away_score": actual["away_score"],
        }

    logged = _logged_win_probability(predictions_table, event_key_value)
    if logged is None:
        try:
            event_prediction.compute_and_cache_event(storage, s3, predictions_table, real_event["event_id"])
            logged = _logged_win_probability(predictions_table, event_key_value)
        except Exception:
            logger.exception("Failed computing a live prediction for bracket game %s", event_key_value)

    predicted_winner = win_probability = None
    if logged is not None:
        probability = logged["home_win_probability"]
        predicted_winner = home_id if probability >= 0.5 else away_id
        win_probability = probability if predicted_winner == home_id else 1 - probability

    return {
        "status": "scheduled",
        "team_a": home_id, "team_b": away_id, "seed_a": seed_a, "seed_b": seed_b,
        "predicted_winner": predicted_winner, "win_probability": win_probability,
    }


def _project_bracket_round(
    round_name: str, pairs: list[tuple[str, str, int | None, int | None]],
    real_matchups: dict[frozenset, dict], storage: FeatureStorage, s3, predictions_table,
    current_ratings: dict[str, float], home_advantage: float,
) -> tuple[dict, list[tuple[str, int | None]]]:
    """Resolves one round's worth of (team_a, team_b, seed_a, seed_b)
    slots, returning that round's display dict alongside
    [(advancing_team, its_own_seed), ...] for the next round to consume."""
    matchups = []
    advancing = []
    for team_a, team_b, seed_a, seed_b in pairs:
        matchup = _resolve_matchup(
            team_a, team_b, seed_a, seed_b, real_matchups, storage, s3, predictions_table,
            current_ratings, home_advantage,
        )
        matchups.append(matchup)
        winner = matchup["predicted_winner"] if matchup["status"] != "final" else matchup["actual_winner"]
        winner_seed = seed_a if winner == team_a else seed_b
        advancing.append((winner, winner_seed))
    return {"round": round_name, "matchups": matchups}, advancing


def _bracket_payload(
    storage: FeatureStorage, s3, predictions_table, season_inputs: dict,
    estimator, model_card: dict, teams: list[str], simulation: dict[str, dict],
) -> dict | None:
    """Builds the 12-team CFP bracket, reconciled against real results as
    they exist right now -- see _resolve_matchup's own docstring for the
    3-state design. Seeds from the season simulation's own projected
    end-of-year wins once the regular season still has games left, else
    real wins -- same real-vs-projected split NFL's/NBA's own
    _bracket_payload use for seeding (2026-08-16 -- this module previously
    always seeded off TODAY's real record regardless of how much season
    remained, which put the bracket's field out of step with the
    standings table's own end-of-season projection; changed to match).
    point_differential/ratings stay at today's real value either way --
    simulate_season doesn't project either forward, same simplification
    NFL's/NBA's own seeding already accepts. None if fewer than
    CFP_FIELD_SIZE teams are tracked (mirrors build_season_projection's
    own simulate_season gate)."""
    if len(teams) < season_simulation.CFP_FIELD_SIZE:
        return None

    regular_season_over = not season_inputs["remaining_games"]
    if regular_season_over:
        wins, losses = season_inputs["wins"], season_inputs["losses"]
    else:
        wins = {team_id: projection["projected_wins"] for team_id, projection in simulation.items()}
        losses = {team_id: projection["projected_losses"] for team_id, projection in simulation.items()}

    model_scores = _batch_score_teams(estimator, model_card, teams, season_inputs, wins, losses, season_inputs["current_ratings"])
    conferences = season_simulation._group_by_conference(season_inputs["team_conference"])
    champions = {
        conference: season_simulation._conference_champion(members, wins, season_inputs["point_differential"])
        for conference, members in conferences.items()
    }
    seeds = season_simulation._select_cfp_field(model_scores, champions)
    seed_number = {team_id: rank + 1 for rank, team_id in enumerate(seeds)}
    one, two, three, four, five, six, seven, eight, nine, ten, eleven, twelve = seeds

    current_season = season_inputs["current_season"]
    real_matchups = _real_postseason_matchups(storage, current_season)
    ratings = season_inputs["current_ratings"]
    home_advantage = season_simulation.DEFAULT_HOME_ADVANTAGE

    round_of_12, round_of_12_advancing = _project_bracket_round(
        "Round of 12",
        [
            (five, twelve, seed_number[five], seed_number[twelve]),
            (six, eleven, seed_number[six], seed_number[eleven]),
            (seven, ten, seed_number[seven], seed_number[ten]),
            (eight, nine, seed_number[eight], seed_number[nine]),
        ],
        real_matchups, storage, s3, predictions_table, ratings, home_advantage,
    )
    r1_5v12, r1_6v11, r1_7v10, r1_8v9 = round_of_12_advancing

    quarterfinals, quarterfinal_advancing = _project_bracket_round(
        "Quarterfinals",
        [
            (one, r1_8v9[0], seed_number[one], r1_8v9[1]),
            (two, r1_5v12[0], seed_number[two], r1_5v12[1]),
            (three, r1_6v11[0], seed_number[three], r1_6v11[1]),
            (four, r1_7v10[0], seed_number[four], r1_7v10[1]),
        ],
        real_matchups, storage, s3, predictions_table, ratings, 0.0,
    )
    qf1, qf2, qf3, qf4 = quarterfinal_advancing

    semifinals, semifinal_advancing = _project_bracket_round(
        "Semifinals",
        [(qf1[0], qf4[0], qf1[1], qf4[1]), (qf2[0], qf3[0], qf2[1], qf3[1])],
        real_matchups, storage, s3, predictions_table, ratings, 0.0,
    )
    sf1, sf2 = semifinal_advancing

    championship, championship_advancing = _project_bracket_round(
        "National Championship", [(sf1[0], sf2[0], sf1[1], sf2[1])],
        real_matchups, storage, s3, predictions_table, ratings, 0.0,
    )

    bracket = {
        "rounds": [round_of_12, quarterfinals, semifinals, championship],
        "champion": championship_advancing[0][0],
    }
    return enrich_bracket_team_names(storage, SPORT, bracket)


def build_season_projection(storage: FeatureStorage, s3, predictions_table) -> dict:
    season_inputs = _season_standings_inputs(storage)
    teams = list(season_inputs["team_conference"])

    simulation: dict[str, dict] = {}
    current_rankings: dict[str, int] = {}
    bracket = None
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
            logger.warning("No promoted %s model -- season simulation and ranking skipped this run", RANKING_MODEL_NAME)
        else:
            # Separate try/except from simulate_season above -- a bug here
            # shouldn't cost the whole run its (already-computed, working)
            # simulation. logger.exception captures the full traceback,
            # unlike the NoPromotedModelError branch above which is an
            # expected, routine condition, not a bug to diagnose.
            try:
                current_rankings = _current_rankings(estimator, model_card, teams, season_inputs)
            except Exception:
                logger.exception("Failed to compute current_rank for %s -- standings will omit it this run", SPORT)

            try:
                bracket = _bracket_payload(storage, s3, predictions_table, season_inputs, estimator, model_card, teams, simulation)
            except Exception:
                logger.exception("Failed to build season bracket")

    standings = sorted(
        (
            {
                "team_id": team_id,
                "conference": season_inputs["team_conference"].get(team_id),
                "wins": season_inputs["wins"].get(team_id, 0),
                "losses": season_inputs["losses"].get(team_id, 0),
                "ties": season_inputs["ties"].get(team_id, 0),
                "current_rank": current_rankings.get(team_id),
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
        "bracket": bracket,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def run_scheduled(storage: FeatureStorage, model_bucket, predictions_table) -> dict:
    """Entry point for the weekly EventBridge Scheduler invoke -- computes the projection
    once and writes it to S3. predictions_table is new (2026-08-16, the bracket
    feature) -- _bracket_payload reads/writes real CFP games' logged predictions
    through it, same table event_prediction.py's own compute_and_cache_event
    already uses."""
    result = build_season_projection(storage, model_bucket, predictions_table)
    model_bucket.put_json(season_projection_key(SPORT), result)
    logger.info("Wrote season projection for %s to S3", SPORT)
    return {"status": "ok"}
