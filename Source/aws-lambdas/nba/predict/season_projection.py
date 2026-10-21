"""
Season-long standings/leaderboard/Cup orchestration -- builds the payload
Terraform/scheduler-nba-season-projection.tf's weekly EventBridge
Scheduler invoke writes to S3 for GET /nba/season (see handler.py for why
that route can't compute this live per-request). Pulls season-wide data
once via FeatureStorage, derives Elo ratings and remaining-schedule
inputs (_season_standings_inputs), runs season_simulation's pure Monte
Carlo logic (play-in-aware playoff bracket plus the NBA Cup in-season
tournament), and scores each tracked player-prop leaderboard using the
same model-loading helpers event_prediction.py uses for a single live
request.
"""
import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import event_prediction
import live_features
import season_simulation
from library.features.common import compute_elo_ratings
from library.features.nba_teams import TEAM_DIVISIONS, is_real_franchise_matchup
from library.serving import bracket_projection
from library.serving.bracket_simulation import BracketSimulation, RealPostseason, elo_rules
from library.serving import season_projection_common
from library.serving.common import enrich_bracket_team_names, enrich_team_standings
from library.serving.nba_reads import _home_and_away
from library.storage.feature_storage import FeatureStorage
from library.storage.season_projections import season_projection_key

logger = logging.getLogger("nba-predict")

# ESPN's season.type convention: 3 is postseason, 5 is the play-in
# tournament, its own distinct type, not folded into postseason. Both
# count as "real playoff bracket" games for the bracket simulation
# below; a coincidental regular-season rematch between the same two teams
# never collides since neither type is 2 (regular season).
POSTSEASON_TYPES = {3, 5}

SPORT = "nba"

# Source of truth is Terraform/scheduler-nba-train-player-prop-model.tf's
# nba_player_prop_stats map, duplicated here as a plain list. Only
# points/rebounds/assists have a season-wide depth-chart-style candidate
# search behind them (see _season_wide_candidate_rows/
# event_prediction.LEADER_CATEGORY_STATS) -- steals/blocks/
# three_pointers_made leaderboards are seeded only from players who've
# already recorded at least one stat line this season.
PLAYER_PROP_STATS = ["points", "rebounds", "assists", "steals", "blocks", "three_pointers_made"]

# Group-play games only; knockout-round games carry different note text
# and are never matched here (the knockout bracket is entirely simulated,
# not sourced from real results -- see season_simulation.simulate_cup).
CUP_GROUP_PLAY_NOTE = "NBA Cup - Group Play"


def _current_season_events(storage: FeatureStorage) -> tuple[list[dict], list[dict], list[dict], int | None]:
    return season_projection_common.current_season_events(storage, SPORT, is_real_franchise_matchup)


def _record_game_result(
    event: dict, entity_id: str, opponent_id: str, is_cup_group_game: bool,
    wins: dict[str, int], losses: dict[str, int], point_differential: dict[str, int],
    cup_wins: dict[str, int], cup_losses: dict[str, int],
) -> None:
    """Credits entity_id's own side of one completed game into
    wins/losses/point_differential (and cup_wins/cup_losses, if this was a
    Cup group-play game) -- no-op if either side's own score is missing."""
    participant = next(p for p in event["participants"] if p.get("entity_id") == entity_id)
    opponent = next(p for p in event["participants"] if p.get("entity_id") == opponent_id)
    score = (participant.get("result") or {}).get("score")
    opponent_score = (opponent.get("result") or {}).get("score")
    if score is None or opponent_score is None:
        return
    won = score > opponent_score
    wins[entity_id] = wins.get(entity_id, 0) + (1 if won else 0)
    losses[entity_id] = losses.get(entity_id, 0) + (0 if won else 1)
    point_differential[entity_id] = point_differential.get(entity_id, 0) + (score - opponent_score)
    if is_cup_group_game:
        cup_wins[entity_id] = cup_wins.get(entity_id, 0) + (1 if won else 0)
        cup_losses[entity_id] = cup_losses.get(entity_id, 0) + (0 if won else 1)


def _completed_game_records(completed: list[dict]) -> tuple[dict, dict, dict, dict, dict]:
    """(wins, losses, point_differential, cup_wins, cup_losses) derived
    from this season's own completed games."""
    wins: dict[str, int] = {}
    losses: dict[str, int] = {}
    point_differential: dict[str, int] = {}
    cup_wins: dict[str, int] = {}
    cup_losses: dict[str, int] = {}
    for event in completed:
        home_away = _home_and_away(event)
        if home_away is None:
            continue
        is_cup_group_game = event.get("tournament_note") == CUP_GROUP_PLAY_NOTE
        for entity_id, opponent_id in (home_away, home_away[::-1]):
            _record_game_result(
                event, entity_id, opponent_id, is_cup_group_game, wins, losses, point_differential,
                cup_wins, cup_losses,
            )
    return wins, losses, point_differential, cup_wins, cup_losses


def _remaining_game_inputs(scheduled: list[dict]) -> tuple[list[tuple[str, str]], list[tuple[str, str]], dict[str, str]]:
    """(remaining_games, remaining_cup_games, team_next_event) from this
    season's own still-scheduled games, earliest first."""
    scheduled_sorted = sorted(scheduled, key=lambda e: e.get("event_date", ""))
    remaining_games = []
    remaining_cup_games = []
    team_next_event: dict[str, str] = {}
    for event in scheduled_sorted:
        home_away = _home_and_away(event)
        if home_away is None:
            continue
        home_id, away_id = home_away
        remaining_games.append((home_id, away_id))
        if event.get("tournament_note") == CUP_GROUP_PLAY_NOTE:
            remaining_cup_games.append((home_id, away_id))
        team_next_event.setdefault(home_id, event["event_key"])
        team_next_event.setdefault(away_id, event["event_key"])
    return remaining_games, remaining_cup_games, team_next_event


def _season_standings_inputs(storage: FeatureStorage) -> dict:
    """Fetches this season's completed+scheduled events once and derives
    everything season_simulation.simulate_season/simulate_cup need, plus
    each team's next scheduled event_key (reused by _leaderboards below)."""
    scheduled, all_completed, completed, current_season = _current_season_events(storage)
    # Wins/losses/point-differential are scoped to just this season --
    # standings reset every year regardless of Elo. compute_elo_ratings
    # below gets the FULL (unscoped) history instead, since it does its
    # own season-boundary regression (see that function's docstring).
    wins, losses, point_differential, cup_wins, cup_losses = _completed_game_records(completed)
    _, current_ratings = compute_elo_ratings(all_completed, as_of_season=current_season)
    remaining_games, remaining_cup_games, team_next_event = _remaining_game_inputs(scheduled)

    return {
        "current_season": current_season,
        "completed_event_keys": {e["event_key"] for e in completed},
        "wins": wins,
        "losses": losses,
        "point_differential": point_differential,
        "cup_wins": cup_wins,
        "cup_losses": cup_losses,
        "current_ratings": current_ratings,
        "remaining_games": remaining_games,
        "remaining_cup_games": remaining_cup_games,
        "team_next_event": team_next_event,
        "games_remaining": Counter(team_id for pair in remaining_games for team_id in pair),
    }


def _season_wide_candidate_rows(storage: FeatureStorage, season_inputs: dict) -> dict[str, list[dict]]:
    """Every candidate likely to lead at least one team in a tracked stat
    category (scoring/rebounding/assists), sourced from each team's own
    next scheduled event's recent-volume search via
    live_features.build_live_event_leader_candidates -- run once per
    unique next event, not per team, since the two teams playing each
    other share one."""
    next_event_keys = {key for key in season_inputs["team_next_event"].values() if key}
    rows_by_category: dict[str, list[dict]] = {"scoring": [], "rebounding": [], "assists": []}

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
                for category in rows_by_category:
                    rows_by_category[category].extend(team_candidates[category])

    return rows_by_category


def _current_season_totals(season_player_stats: list[dict]) -> tuple[dict[str, str], dict[str, dict[str, float]]]:
    return season_projection_common.current_season_totals(season_player_stats, PLAYER_PROP_STATS)


def _season_wide_feature_rows(
    storage: FeatureStorage, season_inputs: dict, current_totals_by_stat: dict[str, dict[str, float]],
    player_team: dict[str, str],
) -> tuple[dict[str, dict], dict[str, set[str]]]:
    """(feature_row_cache, stat_candidates) pre-populated from the
    season-wide recent-volume search. Mutates player_team in place."""
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
    # sport's full event history (see the NFL projection's own fix).
    events = storage.get_all_events(SPORT, status="completed") if remaining else []

    def build_row(next_event_key: str, entity_id: str) -> dict:
        return live_features.build_live_player_features(
            storage, SPORT, next_event_key, entity_id, current_ratings=season_inputs["current_ratings"], events=events,
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
    _project_stat_leaderboard's own docstring for the projection shape."""
    season_player_stats = [
        row for row in storage.get_all_player_game_stats(SPORT)
        if row.get("event_key") in season_inputs["completed_event_keys"]
    ]
    player_team, current_totals_by_stat = _current_season_totals(season_player_stats)
    feature_row_cache, stat_candidates = _season_wide_feature_rows(
        storage, season_inputs, current_totals_by_stat, player_team,
    )

    all_candidates = {entity_id for entity_ids in stat_candidates.values() for entity_id in entity_ids}
    remaining = all_candidates - feature_row_cache.keys()
    _fill_remaining_feature_rows(storage, season_inputs, player_team, feature_row_cache, remaining)

    return {
        stat: _project_stat_leaderboard(
            storage, s3, model_cache, season_inputs, stat, stat_candidates[stat],
            current_totals_by_stat, feature_row_cache, player_team,
        )
        for stat in PLAYER_PROP_STATS
    }


def _cup_payload(storage: FeatureStorage, season_inputs: dict) -> dict | None:
    """Groups the NBA Cup simulation's flat team->probabilities map (see
    season_simulation.simulate_cup) into {"groups": {"Eastern A": [row,
    ...], ...}}. None if this season's groups aren't in
    library.features.nba_cup_groups.CUP_GROUPS yet."""
    cup_simulation = season_simulation.simulate_cup(
        season_inputs["current_season"], season_inputs["cup_wins"], season_inputs["cup_losses"],
        season_inputs["remaining_cup_games"], season_inputs["current_ratings"],
    )
    if cup_simulation is None:
        return None

    rows = [{"team_id": team_id, **data} for team_id, data in cup_simulation.items()]
    rows = enrich_team_standings(storage, SPORT, rows)

    groups: dict[str, list[dict]] = {}
    for row in rows:
        group = row.pop("group")
        groups.setdefault(group, []).append(row)
    for group_rows in groups.values():
        group_rows.sort(key=lambda r: (r["group_wins"], -r["group_losses"]), reverse=True)

    return {"groups": groups}


def _cup_bracket_payload(storage: FeatureStorage, season_inputs: dict) -> dict | None:
    """Deterministic NBA Cup knockout bracket (see
    season_simulation.project_cup_knockout_bracket) -- projected only,
    unlike the main playoff bracket's own _bracket_payload below (no
    real-vs-actual reconciliation for Cup knockout games). None if this
    season's groups aren't in CUP_GROUPS yet."""
    bracket = season_simulation.project_cup_knockout_bracket(
        season_inputs["current_season"], season_inputs["cup_wins"], season_inputs["cup_losses"],
        season_inputs["current_ratings"],
    )
    if bracket is None:
        return None
    return enrich_bracket_team_names(storage, SPORT, bracket)


# Single games; every other round is best-of-7.
SINGLE_GAME_ROUNDS = {"Play-In", "Play-In Elimination"}
FINALS_ROUND = "NBA Finals"
ROUND_ODDS_COLUMNS = [
    ("Playoffs", "Conference Quarterfinals"), ("2nd round", "Conference Semifinals"),
    ("Conf finals", "Conference Finals"), ("Finals", FINALS_ROUND), ("Title", None),
]


def _matchup_rules(ratings: dict[str, float]):
    return elo_rules(ratings, lambda round_name: 1 if round_name in SINGLE_GAME_ROUNDS else 4)


def _conference_rounds(play_round, direct_seeds: list[str], play_in_seeds: list[str], home_advantage: float):
    """One conference: the Play-In (7v8 and 9v10, then 7v8's loser against
    9v10's winner), then the fixed, no-reseed 8-team bracket. Returns
    (rounds, champion)."""
    seed_7, seed_8, seed_9, seed_10 = play_in_seeds
    game1, (game1_advancing,) = play_round("Play-In", [(seed_7, seed_8, 7, 8)], home_advantage)
    game2, (game2_advancing,) = play_round("Play-In", [(seed_9, seed_10, 9, 10)], home_advantage)
    seven = game1_advancing[0]
    game1_loser, game1_loser_seed = (seed_8, 8) if seven == seed_7 else (seed_7, 7)
    game3, (game3_advancing,) = play_round(
        "Play-In Elimination", [(game1_loser, game2_advancing[0], game1_loser_seed, game2_advancing[1])], home_advantage,
    )
    field = direct_seeds + [seven, game3_advancing[0]]
    number = {team: rank + 1 for rank, team in enumerate(field)}
    one, two, three, four, five, six, seven, eight = field

    def pair(a: str, b: str):
        return (a, b, number[a], number[b])

    quarterfinals, qf = play_round(
        "Conference Quarterfinals", [pair(one, eight), pair(four, five), pair(three, six), pair(two, seven)], home_advantage,
    )
    semifinals, sf = play_round(
        "Conference Semifinals", [(qf[0][0], qf[1][0], qf[0][1], qf[1][1]), (qf[2][0], qf[3][0], qf[2][1], qf[3][1])], home_advantage,
    )
    final, (champion,) = play_round("Conference Finals", [(sf[0][0], sf[1][0], sf[0][1], sf[1][1])], home_advantage)
    # Play-In's game 2 sits above game 1: game 1's winner skips to the 2v7
    # quarterfinal at the bottom, while game 2 feeds the elimination game.
    play_in = {"round": "Play-In", "matchups": [game2["matchups"][0], game1["matchups"][0]]}
    return [play_in, game3, quarterfinals, semifinals, final], champion[0]


def _bracket_payload(
    storage: FeatureStorage, s3, predictions_table, season_inputs: dict, simulation: dict[str, dict],
) -> dict:
    """The full NBA playoff bracket (both conferences, play-in included,
    plus the Finals) as one simulated run -- see library.serving.
    bracket_simulation. Seeds from real wins/point differential once the
    regular season is over, else from simulate_season's projected_wins."""
    if not season_inputs["remaining_games"]:
        wins = season_inputs["wins"]
    else:
        wins = {team_id: projection["projected_wins"] for team_id, projection in simulation.items()}
    point_differential = season_inputs["point_differential"]
    conferences = season_simulation._teams_by_conference()
    conference_of = {team: conference for conference, teams in conferences.items() for team in teams}
    home_advantage = season_simulation.DEFAULT_HOME_ADVANTAGE
    seeded = {}
    for conference, teams in conferences.items():
        seeds = season_simulation._seed_conference(teams, wins, point_differential)
        seeded[conference] = (seeds[:season_simulation.DIRECT_PLAYOFF_SEEDS], seeds[season_simulation.DIRECT_PLAYOFF_SEEDS:season_simulation.PLAY_IN_FIELD_SIZE])

    def record(team: str) -> tuple:
        return (wins.get(team, 0), point_differential.get(team, 0))

    def topology(play_round) -> dict:
        rounds_by_conference, champions = {}, []
        for conference, (direct_seeds, play_in_seeds) in seeded.items():
            rounds_by_conference[conference], champion = _conference_rounds(play_round, direct_seeds, play_in_seeds, home_advantage)
            champions.append(champion)
        # Home court in the Finals goes to the better regular-season record.
        home, away = sorted(champions, key=record, reverse=True)
        finals, (winner,) = play_round(FINALS_ROUND, [(home, away, None, None)], home_advantage)
        return {"conferences": rounds_by_conference, "finals": finals["matchups"][0], "champion": winner[0]}

    def tiebreak(team_a: str, team_b: str) -> str | None:
        if record(team_a) == record(team_b):
            return None
        return team_a if record(team_a) > record(team_b) else team_b

    events = bracket_projection.real_postseason_events(
        storage, SPORT, season_inputs["current_season"], lambda event: event.get("season_type") in POSTSEASON_TYPES,
    )
    real = RealPostseason(events, bracket_projection.logged_home_probability(event_prediction, storage, s3, predictions_table, logger))
    engine = BracketSimulation(real, _matchup_rules(season_inputs["current_ratings"]), tiebreak=tiebreak)
    bracket = engine.run(topology, ROUND_ODDS_COLUMNS, group_of=conference_of.get)
    return enrich_bracket_team_names(storage, SPORT, bracket)


def postseason_active(storage: FeatureStorage) -> bool:
    """Whether the daily schedule should rebuild the projection today."""
    return bracket_projection.postseason_active(storage, SPORT, lambda event: event.get("season_type") in POSTSEASON_TYPES)


def build_season_projection(storage: FeatureStorage, s3, predictions_table) -> dict:
    model_cache: dict = {}

    season_inputs = _season_standings_inputs(storage)
    simulation = season_simulation.simulate_season(
        season_inputs["wins"], season_inputs["losses"], season_inputs["point_differential"],
        season_inputs["remaining_games"], season_inputs["current_ratings"],
    )

    # division lets the frontend group standings by division (see
    # season_page.dart) -- informational only for NBA, no seeding benefit
    # (see simulate_season). Sorted by projected_wins descending BEFORE
    # grouping, so each division's own teams are already best-to-worst
    # within their group.
    standings = sorted(
        (
            {
                "team_id": team_id,
                "division": TEAM_DIVISIONS.get(team_id),
                "wins": season_inputs["wins"].get(team_id, 0),
                "losses": season_inputs["losses"].get(team_id, 0),
                **projection,
            }
            for team_id, projection in simulation.items()
        ),
        key=lambda row: row["projected_wins"],
        reverse=True,
    )
    standings = enrich_team_standings(storage, SPORT, standings)

    try:
        cup = _cup_payload(storage, season_inputs)
    except Exception:
        logger.exception("Failed to build NBA Cup projection")
        cup = None

    try:
        cup_bracket = _cup_bracket_payload(storage, season_inputs)
    except Exception:
        logger.exception("Failed to build NBA Cup bracket")
        cup_bracket = None

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
        "cup": cup,
        "cup_bracket": cup_bracket,
        "leaderboards": leaderboards,
        "bracket": bracket,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def run_scheduled(storage: FeatureStorage, model_bucket, predictions_table) -> dict:
    """Entry point for Terraform/scheduler-nba-season-projection.tf's
    weekly EventBridge Scheduler -> Lambda direct invoke -- computes
    build_season_projection() once and writes it to S3 instead of
    returning it through API Gateway. Not wrapped in the same try/except
    handler.py's API-Gateway-triggered routes use: there's no HTTP caller
    waiting on a status code here, so a real failure should propagate and
    show up as a Lambda error/CloudWatch alarm, not get silently reshaped
    into a 500 nobody reads.

    _bracket_payload reads/writes real playoff/play-in games' logged
    predictions through predictions_table, the same table
    event_prediction.py's own compute_and_cache_event uses."""
    result = build_season_projection(storage, model_bucket, predictions_table)
    model_bucket.put_json(season_projection_key(SPORT), result)
    logger.info("Wrote season projection for %s to S3", SPORT)
    return {"status": "ok"}
