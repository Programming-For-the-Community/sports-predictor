"""
Builds the season projection (standings, bowl/CFP/championship
probabilities) that Terraform/scheduler-ncaafb-season-projection.tf's
weekly EventBridge Scheduler invoke writes to S3 for GET /ncaafb/season.
Team outcomes only -- no player-prop leaderboard here.

Pulls this season's data once via FeatureStorage, derives Elo ratings and
each team's conference/record/scoring/strength-of-schedule snapshot
(_season_standings_inputs), and runs season_simulation's Monte Carlo
logic with a ranking-model-backed score_teams callable
(_batch_score_teams).
"""
import logging
from collections import Counter
from datetime import datetime, timezone

import event_prediction
import season_simulation
from library.features.common import compute_elo_ratings
from library.serving import bracket_projection
from library.serving import model_loader, season_projection_common
from library.serving.common import enrich_bracket_team_names, enrich_team_standings
from library.serving.ncaafb_reads import _home_and_away
from library.storage.feature_storage import FeatureStorage
from library.storage.season_projections import season_projection_key

logger = logging.getLogger("ncaafb-predict")

SPORT = "ncaafb"
RANKING_MODEL_NAME = "national-ranking"


def _current_season_events(storage: FeatureStorage) -> tuple[list[dict], list[dict], list[dict], int | None]:
    """(scheduled, all_completed, completed, current_season) -- scheduled/
    completed are scoped to just current_season, all_completed is the full
    (unscoped) history compute_elo_ratings needs for its own season-
    boundary regression."""
    scheduled = storage.get_all_events(SPORT, status="scheduled")
    all_completed = storage.get_all_events(SPORT, status="completed")
    current_season = max(
        (e.get("season") for e in scheduled + all_completed if e.get("season") is not None), default=None,
    )
    scheduled = [e for e in scheduled if e.get("season") == current_season]
    completed = [e for e in all_completed if e.get("season") == current_season]
    return scheduled, all_completed, completed, current_season


def _record_game_result(
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


def _completed_game_records(completed: list[dict]) -> tuple[dict, dict, dict, dict, dict, dict, dict]:
    """(wins, losses, ties, point_differential, team_last_completed_date,
    team_conference, completed_by_team) derived from this season's own
    completed games -- team_conference comes from each event's own
    home_conference/away_conference."""
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
            _record_game_result(event, entity_id, opponent_id, wins, losses, ties, point_differential, team_last_completed_date)

    for team_events in completed_by_team.values():
        team_events.sort(key=lambda e: e.get("event_date", ""), reverse=True)

    return wins, losses, ties, point_differential, team_last_completed_date, team_conference, completed_by_team


_team_rolling_stats = season_projection_common.team_rolling_stats


def _remaining_game_inputs(
    scheduled: list[dict], team_conference: dict[str, str],
) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """(remaining_games, team_next_event) from this season's own
    still-scheduled games, earliest first -- mutates team_conference in
    place with any scheduled-only team not already known from a completed
    game. remaining_games only keeps a pairing when both sides have a
    known conference (excludes FCS buy games)."""
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
    return remaining_games, team_next_event


def _season_standings_inputs(storage: FeatureStorage) -> dict:
    """Fetches this season's completed+scheduled events once and derives
    everything simulate_season and the ranking feature rows need."""
    scheduled, all_completed, completed, current_season = _current_season_events(storage)
    wins, losses, ties, point_differential, team_last_completed_date, team_conference, completed_by_team = (
        _completed_game_records(completed)
    )
    pre_game_ratings, current_ratings = compute_elo_ratings(all_completed, as_of_season=current_season)
    avg_points_scored, avg_points_allowed, win_streak, strength_of_schedule = _team_rolling_stats(
        completed_by_team, pre_game_ratings,
    )
    remaining_games, team_next_event = _remaining_game_inputs(scheduled, team_conference)

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
        "real_current_rank": _real_current_ranks(completed + scheduled),
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
    """Scores every team in one batched adapter.predict call (not one call per team)."""
    return season_projection_common.score_rows(
        estimator, model_card,
        {team_id: _ranking_feature_row(team_id, wins, losses, ratings, season_inputs) for team_id in teams},
    )


def _current_model_scores(estimator, model_card: dict, teams: list[str], season_inputs: dict) -> dict[str, float]:
    """Today's actual (not simulated) ranking-model score per team --
    the SAME model/feature row simulate_season's own score_teams callable
    uses to pick each simulated season's CFP field, scored once against
    real current wins/losses/ratings instead of a simulated future. Lower
    is better (see _batch_score_teams). Used both by _model_rankings
    (below, for the standings table's model_rank column) and by
    _bracket_payload (for real bracket seeding)."""
    return _batch_score_teams(
        estimator, model_card, teams, season_inputs,
        season_inputs["wins"], season_inputs["losses"], season_inputs["current_ratings"],
    )


def _model_rankings(estimator, model_card: dict, teams: list[str], season_inputs: dict) -> dict[str, int]:
    """The national-ranking model's own opinion of today's ranking --
    rank is each team's 1-based position once sorted by
    _current_model_scores, not limited to the top 25, standings itself
    displays whatever's meaningful per row. Shown alongside the real
    polled rank (_real_current_ranks) for comparison, not in place of
    it."""
    return season_projection_common.rank_by_score(teams, _current_model_scores(estimator, model_card, teams, season_inputs))


def _real_current_ranks(events: list[dict]) -> dict[str, int]:
    """Each team's real current national ranking -- CFBD's own weekly
    poll, stamped onto a game's home_current_rank/away_current_rank at
    ingest time (see ingest/enrichment.py), not computed here. The most
    recently dated event carrying a rank wins per team; a team with no
    ranked game this season is simply absent (unranked, not an error)."""
    latest: dict[str, tuple[str, int]] = {}
    for event in events:
        home_away = _home_and_away(event)
        if home_away is None:
            continue
        home_id, away_id = home_away
        event_date = event.get("event_date", "")
        for team_id, rank in ((home_id, event.get("home_current_rank")), (away_id, event.get("away_current_rank"))):
            if rank is None:
                continue
            if team_id not in latest or event_date > latest[team_id][0]:
                latest[team_id] = (event_date, rank)
    return {team_id: rank for team_id, (_, rank) in latest.items()}


_BRACKET = bracket_projection.BracketResolver(
    event_prediction=event_prediction, season_simulation=season_simulation, logger=logger,
)
_logged_win_probability = bracket_projection.logged_win_probability
_predicted_winner_and_probability = bracket_projection.predicted_winner_and_probability
_completed_matchup_row = bracket_projection.completed_matchup_row
_scheduled_matchup_row = _BRACKET.scheduled_matchup_row
_resolve_matchup = _BRACKET.resolve_matchup
_project_bracket_round = _BRACKET.project_bracket_round


def _real_postseason_matchups(storage: FeatureStorage, current_season: int | None) -> dict[frozenset, dict]:
    return bracket_projection.real_postseason_matchups(storage, SPORT, current_season, lambda event: bool(event.get("is_playoff_game")))


def _bracket_payload(
    storage: FeatureStorage, s3, predictions_table, season_inputs: dict,
    estimator, model_card: dict, teams: list[str], simulation: dict[str, dict],
) -> dict | None:
    """Builds the 12-team CFP bracket, reconciled against real results as
    they exist right now -- see _resolve_matchup's own docstring for the
    3-state design. Seeds from the season simulation's own projected
    end-of-year wins once the regular season still has games left, else
    real wins. point_differential/ratings stay at today's real value
    either way -- simulate_season doesn't project either forward. None
    if fewer than CFP_FIELD_SIZE teams are tracked."""
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
    model_rankings: dict[str, int] = {}
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
            # shouldn't cost the whole run its already-computed simulation.
            try:
                model_rankings = _model_rankings(estimator, model_card, teams, season_inputs)
            except Exception:
                logger.exception("Failed to compute model_rank for %s -- standings will omit it this run", SPORT)

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
                "current_rank": season_inputs["real_current_rank"].get(team_id),
                "model_rank": model_rankings.get(team_id),
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
    once and writes it to S3. predictions_table is used by _bracket_payload to
    read/write real CFP games' logged predictions."""
    result = build_season_projection(storage, model_bucket, predictions_table)
    model_bucket.put_json(season_projection_key(SPORT), result)
    logger.info("Wrote season projection for %s to S3", SPORT)
    return {"status": "ok"}
