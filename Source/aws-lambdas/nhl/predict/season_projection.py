"""
NHL season projection: standings with playoff, division and Stanley Cup
odds, the projected playoff bracket, and season leaderboards for the
player props the app shows. Computed weekly by the scheduled invoke
(run_scheduled) and written to S3, where GET /nhl/season reads it.

Standings rows follow the other head-to-head sports' shape. Hockey's
overtime losses ride in `ties`, so the record reads W-L-OTL; `losses` is
regulation losses, and projected_losses counts both kinds.

The bracket is always the projected one from projected final standings.
It does not yet fold in real playoff results once the playoffs start.
"""
import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import event_prediction
import live_features
import season_simulation
from library.features import nhl_dataset, nhl_teams
from library.serving import season_projection_common
from library.serving.common import enrich_bracket_team_names, enrich_team_standings
from library.serving.nhl_reads import LEADER_CATEGORY_STATS, _home_and_away
from library.storage.feature_storage import FeatureStorage
from library.storage.season_projections import season_projection_key

logger = logging.getLogger("nhl-predict")

SPORT = "nhl"
REGULAR_SEASON_TYPE = 2

PLAYER_PROP_STATS = [stat for stats in LEADER_CATEGORY_STATS.values() for stat in stats]
GOALIE_CATEGORY = "goaltending"


def _record_game(event: dict, records: dict[str, Counter]) -> None:
    home_away = _home_and_away(event)
    if home_away is None:
        return
    results = {p["entity_id"]: (p.get("result") or {}) for p in event["participants"]}
    scores = [results[team].get("score") for team in home_away]
    if None in scores or scores[0] == scores[1]:
        return
    winner, loser = home_away if scores[0] > scores[1] else home_away[::-1]
    overtime = bool(event.get("went_to_overtime"))
    records["wins"][winner] += 1
    if overtime:
        records["overtime_losses"][loser] += 1
    else:
        records["losses"][loser] += 1
        records["regulation_wins"][winner] += 1


def _season_inputs(storage: FeatureStorage) -> dict:
    """This season's records, remaining schedule and Elo ratings, plus
    what the leaderboards reuse: each team's next scheduled event and the
    completed-event history, read once."""
    scheduled, all_completed, completed, current_season = season_projection_common.current_season_events(
        storage, SPORT, nhl_teams.is_real_franchise_matchup,
    )
    records = {name: Counter() for name in ("wins", "losses", "overtime_losses", "regulation_wins")}
    regular_season = [event for event in completed if event.get("season_type") == REGULAR_SEASON_TYPE]
    for event in regular_season:
        _record_game(event, records)
    _, ratings = nhl_dataset.franchise_elo_ratings(all_completed, as_of_season=current_season)

    remaining_games, team_next_event = [], {}
    for event in sorted(scheduled, key=lambda e: e.get("event_date", "")):
        home_away = _home_and_away(event)
        if home_away is None:
            continue
        for team_id in home_away:
            team_next_event.setdefault(team_id, event["event_key"])
        if event.get("season_type") == REGULAR_SEASON_TYPE:
            remaining_games.append(home_away)

    return {
        "current_season": current_season,
        "completed_event_keys": {e["event_key"] for e in regular_season},
        "season_start_date": min((e["event_date"] for e in regular_season if e.get("event_date")), default=None),
        "history": all_completed,
        **{name: dict(counter) for name, counter in records.items()},
        "current_ratings": ratings,
        "remaining_games": remaining_games,
        "team_next_event": team_next_event,
        "games_remaining": Counter(team_id for pair in remaining_games for team_id in pair),
    }


def _standings(storage: FeatureStorage, season_inputs: dict, simulation: dict[str, dict]) -> list[dict]:
    rows = [
        {
            "team_id": team_id,
            "division": nhl_teams.TEAM_DIVISIONS.get(team_id),
            "wins": season_inputs["wins"].get(team_id, 0),
            "losses": season_inputs["losses"].get(team_id, 0),
            "ties": season_inputs["overtime_losses"].get(team_id, 0),
            **projection,
        }
        for team_id, projection in simulation.items()
    ]
    rows.sort(key=lambda row: row["projected_points"], reverse=True)
    return enrich_team_standings(storage, SPORT, rows)


def _bracket(storage: FeatureStorage, season_inputs: dict, simulation: dict[str, dict]) -> dict:
    """The bracket implied by projected final standings. Regulation wins
    are projected at each team's current share of its wins."""
    points = {team: row["projected_points"] for team, row in simulation.items()}
    wins = {team: row["projected_wins"] for team, row in simulation.items()}
    regulation_wins = {}
    for team, projected in wins.items():
        current = season_inputs["wins"].get(team, 0)
        share = season_inputs["regulation_wins"].get(team, 0) / current if current else 1 - season_simulation.OVERTIME_RATE
        regulation_wins[team] = projected * share
    bracket = season_simulation.project_bracket(points, regulation_wins, wins, season_inputs["current_ratings"])
    return enrich_bracket_team_names(storage, SPORT, bracket)


def _category_rows(team: dict, category: str) -> list[dict]:
    if category != GOALIE_CATEGORY:
        return team["skaters"]
    return [team["goalie"]] if team.get("goalie") else []


def _candidate_rows(storage: FeatureStorage, season_inputs: dict, events: list[dict]) -> dict[str, list[dict]]:
    """Leader-candidate feature rows from every team's next game, by
    category -- built once per next event, which two teams share."""
    next_event_keys = {key for key in season_inputs["team_next_event"].values() if key}
    rows_by_category: dict[str, list[dict]] = {category: [] for category in LEADER_CATEGORY_STATS}

    def candidates_for(event_key: str) -> dict | None:
        try:
            return live_features.build_live_event_leader_candidates(storage, SPORT, event_key, events=events)
        except Exception:
            logger.exception("Failed building season-wide candidates for %s", event_key)
            return None

    with ThreadPoolExecutor(max_workers=max(1, min(len(next_event_keys), 10))) as executor:
        teams = [
            candidates[side]
            for candidates in executor.map(candidates_for, next_event_keys) if candidates is not None
            for side in ("home", "away")
        ]
    for team in teams:
        for category, rows in rows_by_category.items():
            rows.extend(_category_rows(team, category))
    return rows_by_category


def _leaderboards(storage: FeatureStorage, s3, season_inputs: dict) -> dict:
    """Top-10 season leaderboard per displayed prop stat: the current
    total, and that plus the player's predicted per-game value over his
    team's remaining games."""
    season_player_stats = [
        row for row in storage.get_all_player_game_stats(SPORT, since_date=season_inputs["season_start_date"])
        if row.get("event_key") in season_inputs["completed_event_keys"]
    ] if season_inputs["season_start_date"] else []
    player_team, current_totals_by_stat = season_projection_common.current_season_totals(season_player_stats, PLAYER_PROP_STATS)
    events = season_inputs["history"]
    feature_row_cache, stat_candidates = season_projection_common.season_wide_feature_rows(
        _candidate_rows(storage, season_inputs, events), PLAYER_PROP_STATS, LEADER_CATEGORY_STATS,
        current_totals_by_stat, player_team,
    )
    # Only each stat's current leaders need a row beyond the candidates
    # above -- every player in the league has a total.
    leaders = {
        entity_id
        for stat in PLAYER_PROP_STATS
        for entity_id in sorted(current_totals_by_stat[stat], key=current_totals_by_stat[stat].get, reverse=True)[:25]
    }
    season_projection_common.fill_remaining_feature_rows(
        season_inputs, player_team, feature_row_cache, leaders - feature_row_cache.keys(),
        lambda next_event_key, entity_id: live_features.build_live_player_features(
            storage, SPORT, next_event_key, entity_id, events=events,
        ),
        live_features.EventNotFoundError, logger,
    )
    model_cache: dict = {}
    return {
        stat: season_projection_common.project_stat_leaderboard(
            storage, SPORT, s3, model_cache, season_inputs, stat, stat_candidates[stat], current_totals_by_stat,
            feature_row_cache, player_team, event_prediction=event_prediction,
            project_leaderboard=season_simulation.project_leaderboard,
        )
        for stat in PLAYER_PROP_STATS
    }


def build_season_projection(storage: FeatureStorage, s3) -> dict:
    season_inputs = _season_inputs(storage)
    simulation = season_simulation.simulate_season(
        season_inputs["wins"], season_inputs["losses"], season_inputs["overtime_losses"],
        season_inputs["regulation_wins"], season_inputs["remaining_games"], season_inputs["current_ratings"],
    )

    try:
        leaderboards = _leaderboards(storage, s3, season_inputs)
    except Exception:
        logger.exception("Failed to build season leaderboards")
        leaderboards = None

    try:
        bracket = _bracket(storage, season_inputs, simulation)
    except Exception:
        logger.exception("Failed to build season bracket")
        bracket = None

    return {
        "sport": SPORT,
        "season": season_inputs["current_season"],
        "standings": _standings(storage, season_inputs, simulation),
        "leaderboards": leaderboards,
        "bracket": bracket,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def run_scheduled(storage: FeatureStorage, model_bucket) -> dict:
    """Entry point for the weekly EventBridge Scheduler invoke
    (Terraform/scheduler-nhl-season-projection.tf): computes the
    projection and writes it to S3. A failure propagates so it shows up
    as a Lambda error."""
    result = build_season_projection(storage, model_bucket)
    model_bucket.put_json(season_projection_key(SPORT), result)
    logger.info("Wrote season projection for %s to S3", SPORT)
    return {"status": "ok"}
