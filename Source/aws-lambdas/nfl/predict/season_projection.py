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
from library.serving.bracket_simulation import BracketSimulation, RealPostseason, elo_rules
from library.serving import season_projection_common
from library.serving.common import enrich_bracket_team_names, enrich_team_standings
from library.serving.nfl_reads import _home_and_away
from library.storage.feature_storage import FeatureStorage
from library.storage.season_projections import season_projection_key

logger = logging.getLogger("nfl-predict")

SPORT = "nfl"

# ESPN's own season.type convention -- 3 is postseason. Only
# postseason-flagged events feed the bracket simulation
# (_bracket_payload), so a coincidental regular-season rematch
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

    # The sport's full event history, read once and shared -- each call
    # would otherwise re-read it.
    events = storage.get_all_events(SPORT, status="completed") if next_event_keys else []

    def _candidates_for_event(event_key: str) -> dict | None:
        try:
            return live_features.build_live_event_leader_candidates(storage, SPORT, event_key, events=events)
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
    # Read once for every row -- each row would otherwise re-read the
    # sport's full event history, which took the weekly run past Lambda's
    # 10-minute limit once there were a few hundred candidates.
    events = storage.get_all_events(SPORT, status="completed") if remaining else []

    def build_row(next_event_key: str, entity_id: str) -> dict:
        return live_features.build_live_player_features(
            storage, SPORT, next_event_key, entity_id, current_ratings=season_inputs["current_ratings"],
            team_last_event_dates=season_inputs["team_last_completed_date"], events=events,
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


SUPER_BOWL = "Super Bowl"
ROUND_ODDS_COLUMNS = [
    ("Divisional", "Divisional"), ("Conf champ", "Conference Championship"), (SUPER_BOWL, SUPER_BOWL), ("Title", None),
]


def _conference_rounds(play_round, seeds: list[str], home_advantage: float):
    """The reseeded 7-team conference bracket: 1 has a bye; 2v7, 3v6, 4v5;
    then 1 hosts the lowest seed left while the other two meet. Returns
    (rounds, champion)."""
    number = {team: rank + 1 for rank, team in enumerate(seeds)}
    one, two, three, four, five, six, seven = seeds
    wild_card, advancing = play_round(
        "Wild Card", [(a, b, number[a], number[b]) for a, b in ((two, seven), (three, six), (four, five))], home_advantage,
    )
    remaining = sorted(advancing, key=lambda pair: pair[1])
    lowest, (other_a, other_b) = remaining[-1], remaining[:-1]
    divisional, (div_1, div_2) = play_round(
        "Divisional",
        [(one, lowest[0], number[one], lowest[1]), (other_a[0], other_b[0], other_a[1], other_b[1])],
        home_advantage,
    )
    championship, (champion,) = play_round(
        "Conference Championship", [(div_1[0], div_2[0], div_1[1], div_2[1])], home_advantage,
    )
    return [wild_card, divisional, championship], champion[0]


def _bracket_payload(
    storage: FeatureStorage, s3, predictions_table, season_inputs: dict, simulation: dict[str, dict],
) -> dict:
    """The full playoff bracket (both conferences + Super Bowl) as one
    simulated run -- see library.serving.bracket_simulation. Seeds from real
    wins/point differential once the regular season is over, else from
    simulate_season's projected_wins."""
    if not season_inputs["remaining_games"]:
        wins = season_inputs["wins"]
    else:
        wins = {team_id: projection["projected_wins"] for team_id, projection in simulation.items()}
    point_differential = season_inputs["point_differential"]
    conferences = season_simulation._divisions_by_conference()
    seeded = {
        conference: season_simulation._seed_conference(division_teams, wins, point_differential)[0]
        for conference, division_teams in conferences.items()
    }
    conference_of = {team: conference for conference, seeds in seeded.items() for team in seeds}
    home_advantage = season_simulation.DEFAULT_HOME_ADVANTAGE

    def record(team: str) -> tuple:
        return (wins.get(team, 0), point_differential.get(team, 0))

    def topology(play_round) -> dict:
        rounds_by_conference, champions = {}, []
        for conference, seeds in seeded.items():
            rounds_by_conference[conference], champion = _conference_rounds(play_round, seeds, home_advantage)
            champions.append(champion)
        # A neutral site.
        super_bowl, (winner,) = play_round(SUPER_BOWL, [(champions[0], champions[1], None, None)], 0.0)
        return {"conferences": rounds_by_conference, "super_bowl": super_bowl["matchups"][0], "champion": winner[0]}

    def tiebreak(team_a: str, team_b: str) -> str | None:
        if record(team_a) == record(team_b):
            return None
        return team_a if record(team_a) > record(team_b) else team_b

    events = bracket_projection.real_postseason_events(
        storage, SPORT, season_inputs["current_season"], lambda event: event.get("season_type") == POSTSEASON_TYPE,
    )
    real = RealPostseason(events, bracket_projection.logged_home_probability(event_prediction, storage, s3, predictions_table, logger))
    engine = BracketSimulation(real, elo_rules(season_inputs["current_ratings"]), tiebreak=tiebreak)
    bracket = engine.run(topology, ROUND_ODDS_COLUMNS, group_of=conference_of.get)
    return enrich_bracket_team_names(storage, SPORT, bracket)


def postseason_active(storage: FeatureStorage) -> bool:
    """Whether the daily schedule should rebuild the projection today."""
    return bracket_projection.postseason_active(storage, SPORT, lambda event: event.get("season_type") == POSTSEASON_TYPE)


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
