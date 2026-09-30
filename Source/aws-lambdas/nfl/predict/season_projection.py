"""
Season-long standings/leaderboard orchestration -- builds the payload
the weekly EventBridge Scheduler invoke writes to S3 for GET /nfl/season.
Pulls season-wide data once via FeatureStorage, derives Elo ratings and
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
import season_simulation
from library.features.common import compute_elo_ratings
from library.features.nfl_teams import TEAM_DIVISIONS, is_real_franchise_matchup
from library.serving import bracket_projection
from library.serving import season_projection_common
from library.serving.common import enrich_bracket_team_names, enrich_team_standings
from library.serving.nfl_reads import _home_and_away
from library.storage.feature_storage import FeatureStorage
from library.storage.season_projections import season_projection_key

logger = logging.getLogger("nfl-predict")

SPORT = "nfl"

# ESPN's own season.type convention -- 3 is postseason. Only
# postseason-flagged events are matched against a bracket slot in
# _real_postseason_matchups, so a coincidental regular-season rematch
# never gets mistaken for a real playoff meeting.
POSTSEASON_TYPE = 3

PLAYER_PROP_STATS = [
    "passing_yards", "passing_touchdowns", "rushing_yards", "rushing_touchdowns",
    "receiving_yards", "receiving_touchdowns", "defensive_sacks",
]


def _current_season_events(storage: FeatureStorage) -> tuple[list[dict], list[dict], list[dict], int | None]:
    return season_projection_common.current_season_events(storage, SPORT, is_real_franchise_matchup)


_record_game_result = season_projection_common.record_game_result_with_ties


def _completed_game_records(completed: list[dict]) -> tuple[dict, dict, dict, dict, dict]:
    """(wins, losses, ties, point_differential, team_last_completed_date)
    derived from this season's own completed games."""
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
            _record_game_result(event, entity_id, opponent_id, wins, losses, ties, point_differential, team_last_completed_date)
    return wins, losses, ties, point_differential, team_last_completed_date


def _remaining_game_inputs(scheduled: list[dict]) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """(remaining_games, team_next_event) from this season's own
    still-scheduled games, earliest first."""
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
    return remaining_games, team_next_event


def _season_standings_inputs(storage: FeatureStorage) -> dict:
    """Fetches this season's completed+scheduled events once and derives
    everything season_simulation.simulate_season needs, plus each team's
    next scheduled event_key (reused by _leaderboards below)."""
    scheduled, all_completed, completed, current_season = _current_season_events(storage)
    # Wins/losses/point-differential are scoped to just this season --
    # standings reset every year regardless of Elo. compute_elo_ratings
    # below gets the FULL (unscoped) history instead, since it does its
    # own season-boundary regression.
    wins, losses, ties, point_differential, team_last_completed_date = _completed_game_records(completed)
    _, current_ratings = compute_elo_ratings(all_completed, as_of_season=current_season)
    remaining_games, team_next_event = _remaining_game_inputs(scheduled)

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


def _current_season_totals(season_player_stats: list[dict]) -> tuple[dict[str, str], dict[str, dict[str, float]]]:
    return season_projection_common.current_season_totals(season_player_stats, PLAYER_PROP_STATS)


def _depth_chart_feature_rows(
    storage: FeatureStorage, season_inputs: dict, current_totals_by_stat: dict[str, dict[str, float]],
    player_team: dict[str, str],
) -> tuple[dict[str, dict], dict[str, set[str]]]:
    """(feature_row_cache, stat_candidates) pre-populated from the
    depth-chart-sourced rows. Mutates player_team in place."""
    return season_projection_common.season_wide_feature_rows(
        _season_wide_candidate_rows(storage, season_inputs), PLAYER_PROP_STATS, event_prediction.LEADER_CATEGORY_STATS,
        current_totals_by_stat, player_team,
    )


def _fill_remaining_feature_rows(
    storage: FeatureStorage, season_inputs: dict, player_team: dict[str, str],
    feature_row_cache: dict[str, dict], remaining: set[str],
) -> None:
    """Live feature rows for candidates the season-wide pass didn't cover
    -- mutates feature_row_cache in place."""
    def build_row(next_event_key: str, entity_id: str) -> dict:
        return live_features.build_live_player_features(
            storage, SPORT, next_event_key, entity_id, current_ratings=season_inputs["current_ratings"],
            team_last_event_dates=season_inputs["team_last_completed_date"],
        )

    season_projection_common.fill_remaining_feature_rows(
        season_inputs, player_team, feature_row_cache, remaining, build_row, live_features.EventNotFoundError, logger,
    )


def _project_stat_leaderboard(
    storage: FeatureStorage, s3, model_cache: dict, season_inputs: dict, stat: str, candidates: set[str],
    current_totals_by_stat: dict[str, dict[str, float]], feature_row_cache: dict[str, dict],
    player_team: dict[str, str],
) -> list[dict]:
    return season_projection_common.project_stat_leaderboard(
        storage, SPORT, s3, model_cache, season_inputs, stat, candidates, current_totals_by_stat, feature_row_cache,
        player_team, event_prediction=event_prediction, project_leaderboard=season_simulation.project_leaderboard,
    )


def _leaderboards(storage: FeatureStorage, s3, model_cache: dict, season_inputs: dict) -> dict:
    """Top-10 season-long leaderboard per tracked player-prop stat -- see
    _project_stat_leaderboard's own docstring for the projection shape.
    Each stat is scored independently: one stat's model failing (a stale
    promoted artifact pickled under a since-upgraded library version, say)
    omits just that stat rather than the whole leaderboards response --
    every other stat's own model is unrelated and still worth serving."""
    season_player_stats = [
        row for row in storage.get_all_player_game_stats(SPORT)
        if row.get("event_key") in season_inputs["completed_event_keys"]
    ]
    player_team, current_totals_by_stat = _current_season_totals(season_player_stats)
    feature_row_cache, stat_candidates = _depth_chart_feature_rows(
        storage, season_inputs, current_totals_by_stat, player_team,
    )

    all_candidates = {entity_id for entity_ids in stat_candidates.values() for entity_id in entity_ids}
    remaining = all_candidates - feature_row_cache.keys()
    _fill_remaining_feature_rows(storage, season_inputs, player_team, feature_row_cache, remaining)

    leaderboards: dict[str, list[dict]] = {}
    for stat in PLAYER_PROP_STATS:
        try:
            leaderboards[stat] = _project_stat_leaderboard(
                storage, s3, model_cache, season_inputs, stat, stat_candidates[stat],
                current_totals_by_stat, feature_row_cache, player_team,
            )
        except Exception:
            logger.exception("Failed projecting leaderboard for stat %s -- omitting just this stat", stat)
    return leaderboards


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
    return bracket_projection.real_postseason_matchups(storage, SPORT, current_season, lambda event: event.get("season_type") == POSTSEASON_TYPE)


def _project_conference_bracket(
    seeds: list[str], real_matchups: dict[frozenset, dict], storage: FeatureStorage, s3, predictions_table,
    current_ratings: dict[str, float], home_advantage: float,
) -> tuple[list[dict], str]:
    """Reseeded 7-team playoff topology; each matchup goes through
    _resolve_matchup's real-vs-projected reconciliation instead of a
    pure Elo pick. Returns (rounds, champion)."""
    seed_number = {team_id: rank + 1 for rank, team_id in enumerate(seeds)}
    one, two, three, four, five, six, seven = seeds

    wild_card_round, wild_card_advancing = _project_bracket_round(
        "Wild Card",
        [(a, b, seed_number[a], seed_number[b]) for a, b in ((two, seven), (three, six), (four, five))],
        real_matchups, storage, s3, predictions_table, current_ratings, home_advantage,
    )

    divisional_field = sorted(wild_card_advancing, key=lambda pair: pair[1])
    lowest_remaining = divisional_field[-1]
    other_two = divisional_field[:-1]
    divisional_round, divisional_advancing = _project_bracket_round(
        "Divisional",
        [
            (one, lowest_remaining[0], seed_number[one], lowest_remaining[1]),
            (other_two[0][0], other_two[1][0], other_two[0][1], other_two[1][1]),
        ],
        real_matchups, storage, s3, predictions_table, current_ratings, home_advantage,
    )

    championship_round, championship_advancing = _project_bracket_round(
        "Conference Championship",
        [(divisional_advancing[0][0], divisional_advancing[1][0], divisional_advancing[0][1], divisional_advancing[1][1])],
        real_matchups, storage, s3, predictions_table, current_ratings, home_advantage,
    )

    return [wild_card_round, divisional_round, championship_round], championship_advancing[0][0]


def _bracket_payload(
    storage: FeatureStorage, s3, predictions_table, season_inputs: dict, simulation: dict[str, dict],
) -> dict:
    """Builds the full playoff bracket (both conferences + Super Bowl),
    reconciled against real postseason results as they exist right now.
    Reseeds from real wins/point-differential once the regular season is
    actually over (season_inputs["remaining_games"] empty); otherwise
    from simulate_season's own projected_wins."""
    regular_season_over = not season_inputs["remaining_games"]
    if regular_season_over:
        wins, point_differential = season_inputs["wins"], season_inputs["point_differential"]
    else:
        wins = {team_id: projection["projected_wins"] for team_id, projection in simulation.items()}
        point_differential = season_inputs["point_differential"]

    conferences = season_simulation._divisions_by_conference()
    current_season = season_inputs["current_season"]
    real_matchups = _real_postseason_matchups(storage, current_season)

    conference_results: dict[str, list[dict]] = {}
    champions: dict[str, str] = {}
    for conference, division_teams in conferences.items():
        seeds, _ = season_simulation._seed_conference(division_teams, wins, point_differential)
        rounds, champion = _project_conference_bracket(
            seeds, real_matchups, storage, s3, predictions_table, season_inputs["current_ratings"],
            season_simulation.DEFAULT_HOME_ADVANTAGE,
        )
        conference_results[conference] = rounds
        champions[conference] = champion

    (_, champion_a), (_, champion_b) = champions.items()
    super_bowl, super_bowl_advancing = _project_bracket_round(
        "Super Bowl", [(champion_a, champion_b, None, None)],
        real_matchups, storage, s3, predictions_table, season_inputs["current_ratings"], 0.0,
    )

    bracket = {
        "conferences": conference_results,
        "super_bowl": super_bowl["matchups"][0],
        "champion": super_bowl_advancing[0][0],
    }
    return enrich_bracket_team_names(storage, SPORT, bracket)


def build_season_projection(storage: FeatureStorage, s3, predictions_table) -> dict:
    model_cache: dict = {}

    season_inputs = _season_standings_inputs(storage)
    simulation = season_simulation.simulate_season(
        season_inputs["wins"], season_inputs["losses"], season_inputs["point_differential"],
        season_inputs["remaining_games"], season_inputs["current_ratings"],
    )

    # division lets the frontend group standings by division without
    # duplicating TEAM_DIVISIONS client-side. Sorted by projected_wins
    # descending before grouping, so each division's own teams are
    # already best-to-worst within their group.
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
    standings = enrich_team_standings(storage, SPORT, standings)

    try:
        leaderboards = _leaderboards(storage, s3, model_cache, season_inputs)
    except Exception:
        logger.exception("Failed to build season leaderboards")
        leaderboards = None

    try:
        bracket = _bracket_payload(storage, s3, predictions_table, season_inputs, simulation)
    except Exception:
        logger.exception("Failed to build season bracket")
        bracket = None

    return {
        "sport": SPORT,
        "season": season_inputs["current_season"],
        "standings": standings,
        "leaderboards": leaderboards,
        "bracket": bracket,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def run_scheduled(storage: FeatureStorage, model_bucket, predictions_table) -> dict:
    """Entry point for the weekly EventBridge Scheduler -> Lambda direct
    invoke -- computes build_season_projection() once and writes it to
    S3 instead of returning it through API Gateway. Not wrapped in the
    same try/except handler.py's API-Gateway-triggered routes use: there
    is no HTTP caller waiting on a status code here, so a real failure
    should propagate and show up as a Lambda error/CloudWatch alarm."""
    result = build_season_projection(storage, model_bucket, predictions_table)
    model_bucket.put_json(season_projection_key(SPORT), result)
    logger.info("Wrote season projection for %s to S3", SPORT)
    return {"status": "ok"}
