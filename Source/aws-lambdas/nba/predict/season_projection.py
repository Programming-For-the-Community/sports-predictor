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

from boto3.dynamodb.conditions import Key

import event_prediction
import live_features
import model_loader
import season_simulation
from library.features.common import compute_elo_ratings
from library.features.nba_teams import TEAM_DIVISIONS, is_real_franchise_matchup
from library.serving.common import enrich_bracket_team_names, enrich_team_standings
from library.serving.nba_reads import WIN_PROBABILITY_MODEL, _actual_result, _home_and_away
from library.storage.feature_storage import FeatureStorage
from library.storage.season_projections import season_projection_key

logger = logging.getLogger("nba-predict")

# ESPN's season.type convention: 3 is postseason, 5 is the play-in
# tournament, its own distinct type, not folded into postseason. Both
# count as "real playoff bracket" games for _real_postseason_matchups
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


def _season_standings_inputs(storage: FeatureStorage) -> dict:
    """Fetches this season's completed+scheduled events once and derives
    everything season_simulation.simulate_season/simulate_cup need, plus
    each team's next scheduled event_key (reused by _leaderboards below)."""
    # Excludes the All-Star Game and any other exhibition matchup -- its
    # roster "teams" aren't real franchises (see
    # library.features.nba_teams.is_real_franchise_matchup), so a played
    # one would otherwise count as a real win/loss and Elo update for a
    # non-existent team_id.
    scheduled = [e for e in storage.get_all_events(SPORT, status="scheduled") if is_real_franchise_matchup(e)]
    all_completed = [e for e in storage.get_all_events(SPORT, status="completed") if is_real_franchise_matchup(e)]
    current_season = max(
        (e.get("season") for e in scheduled + all_completed if e.get("season") is not None), default=None,
    )
    scheduled = [e for e in scheduled if e.get("season") == current_season]
    # Wins/losses/point-differential are scoped to just this season --
    # standings reset every year regardless of Elo. compute_elo_ratings
    # below gets the FULL (unscoped) history instead, since it does its
    # own season-boundary regression (see that function's docstring).
    completed = [e for e in all_completed if e.get("season") == current_season]

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
            participant = next(p for p in event["participants"] if p.get("entity_id") == entity_id)
            opponent = next(p for p in event["participants"] if p.get("entity_id") == opponent_id)
            score = (participant.get("result") or {}).get("score")
            opponent_score = (opponent.get("result") or {}).get("score")
            if score is None or opponent_score is None:
                continue
            won = score > opponent_score
            wins[entity_id] = wins.get(entity_id, 0) + (1 if won else 0)
            losses[entity_id] = losses.get(entity_id, 0) + (0 if won else 1)
            point_differential[entity_id] = point_differential.get(entity_id, 0) + (score - opponent_score)
            if is_cup_group_game:
                cup_wins[entity_id] = cup_wins.get(entity_id, 0) + (1 if won else 0)
                cup_losses[entity_id] = cup_losses.get(entity_id, 0) + (0 if won else 1)

    _, current_ratings = compute_elo_ratings(all_completed, as_of_season=current_season)

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
                for category in rows_by_category:
                    rows_by_category[category].extend(team_candidates[category])

    return rows_by_category


def _leaderboards(storage: FeatureStorage, s3, model_cache: dict, season_inputs: dict) -> dict:
    """Top-10 season-long leaderboard per tracked player-prop stat,
    projected as current season-to-date total plus (their own model's
    prediction for their team's next scheduled game * games remaining)."""
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

    feature_row_cache: dict[str, dict] = {}
    stat_candidates: dict[str, set[str]] = {stat: set(current_totals_by_stat[stat]) for stat in PLAYER_PROP_STATS}
    for category, rows in _season_wide_candidate_rows(storage, season_inputs).items():
        for row in rows:
            entity_id = row["entity_id"]
            feature_row_cache[entity_id] = row
            player_team.setdefault(entity_id, row.get("team_id"))
            for stat in event_prediction.LEADER_CATEGORY_STATS[category]:
                stat_candidates[stat].add(entity_id)

    # Any candidate not already covered above (this-season stats exist,
    # but they weren't in a season-wide recent-volume search -- a
    # steals/blocks/threes-only standout, or someone who fell just
    # outside their category's own candidate limit) still needs its own
    # feature row built.
    all_candidates = {entity_id for entity_ids in stat_candidates.values() for entity_id in entity_ids}
    remaining = all_candidates - feature_row_cache.keys()

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

    with ThreadPoolExecutor(max_workers=max(1, min(len(remaining), 10))) as executor:
        for entity_id, feature_row in executor.map(_build_row, remaining):
            if feature_row is not None:
                feature_row_cache[entity_id] = feature_row

    leaderboards: dict[str, list[dict]] = {}
    for stat in PLAYER_PROP_STATS:
        candidates = stat_candidates[stat]
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

        top = season_simulation.project_leaderboard(current_totals, per_game_projections, games_remaining, top_n=10)
        for row in top:
            entity = storage.get_entity(SPORT, row["entity_id"], "player")
            if entity and entity.get("name"):
                row["name"] = entity["name"]
        leaderboards[stat] = top

    return leaderboards


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


def _real_postseason_matchups(storage: FeatureStorage, current_season: int | None) -> dict[frozenset, dict]:
    """{frozenset({home_id, away_id}): event} for every real playoff or
    play-in game (season_type in POSTSEASON_TYPES) this season, scheduled
    or completed -- _resolve_matchup checks here before falling back to
    the model's own deterministic pick for a bracket slot. Play-In only --
    every other round is a best-of-7 series (see _real_postseason_series),
    where a single "the" event per pair doesn't make sense (a real series
    is more than one game between the same two teams); this stays a
    single-event-per-pair lookup because Play-In genuinely is single
    elimination."""
    result: dict[frozenset, dict] = {}
    for status in ("scheduled", "completed"):
        for event in storage.get_all_events(SPORT, status=status):
            if event.get("season") != current_season or event.get("season_type") not in POSTSEASON_TYPES:
                continue
            home_away = _home_and_away(event)
            if home_away is None:
                continue
            result[frozenset(home_away)] = event
    return result


def _real_postseason_series(storage: FeatureStorage, current_season: int | None) -> dict[frozenset, list[dict]]:
    """{frozenset({team_a, team_b}): [games chronological]} for every real
    playoff/play-in game (season_type in POSTSEASON_TYPES) this season --
    every real game between a pair, not just one, since a real playoff
    series (Conference Quarterfinals onward) is best-of-7 -- _resolve_series_matchup
    reads a pair's whole game list to know the series' running record and
    which game (if any) is next. Sorted by event_date so "the next
    unplayed game" is well-defined."""
    by_pair: dict[frozenset, list[dict]] = {}
    for status in ("scheduled", "completed"):
        for event in storage.get_all_events(SPORT, status=status):
            if event.get("season") != current_season or event.get("season_type") not in POSTSEASON_TYPES:
                continue
            home_away = _home_and_away(event)
            if home_away is None:
                continue
            by_pair.setdefault(frozenset(home_away), []).append(event)
    for games in by_pair.values():
        games.sort(key=lambda e: e.get("event_date") or "")
    return by_pair


def _logged_win_probability(predictions_table, event_key_value: str) -> dict | None:
    """This event's own logged win-probability prediction, or None if
    nobody's ever requested one."""
    rows = predictions_table.query(Key("event_key").eq(event_key_value))
    row = next((r for r in rows if r["model_key"].startswith(f"MODEL#{WIN_PROBABILITY_MODEL}#")), None)
    return row["predicted_value"] if row else None


def _resolve_matchup(
    team_a: str, team_b: str, seed_a: int | None, seed_b: int | None,
    real_matchups: dict[frozenset, dict], storage: FeatureStorage, s3, predictions_table,
    current_ratings: dict[str, float], home_advantage: float,
) -> dict:
    """Resolves one bracket slot: (1) no real game exists yet -- the
    model's own deterministic pick ("status": "projected"); (2) a real
    game exists and is completed -- the actual result plus whatever was
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


def _series_record(team_a: str, team_b: str, games: list[dict]) -> tuple[int, int]:
    """(wins_a, wins_b) across a chronological list of real games between
    team_a/team_b -- keyed by team_id, not each game's own home/away role,
    since a series' host alternates by game (the real 2-2-1-1-1 format)
    while team_a/team_b stay fixed to the bracket slot's own seeded
    identities. Only completed games count; a scheduled-but-unplayed game
    contributes nothing yet."""
    wins_a = wins_b = 0
    for game in games:
        if game.get("status") != "completed":
            continue
        actual = _actual_result(game)
        home_id, away_id = _home_and_away(game)
        winner_id = home_id if actual["home_won"] else away_id
        if winner_id == team_a:
            wins_a += 1
        elif winner_id == team_b:
            wins_b += 1
    return wins_a, wins_b


def _resolve_series_matchup(
    team_a: str, team_b: str, seed_a: int | None, seed_b: int | None,
    real_series: dict[frozenset, list[dict]], storage: FeatureStorage, s3, predictions_table,
    current_ratings: dict[str, float], home_advantage: float,
) -> dict:
    """Best-of-7 sibling of _resolve_matchup -- every real NBA playoff
    round except Play-In (Conference Quarterfinals, Conference Semifinals,
    Conference Finals, NBA Finals) is a series, not a single game, so this tracks a
    running win count per side across every real game found for the pair
    instead of resolving just one (see _series_record). "final" means the
    SERIES is decided (someone reached 4 wins), not that one game
    finished; "wins_a"/"wins_b" are always present (0/0 for a series that
    hasn't started yet) so the frontend can show a running record like
    "DEN leads 3-1" the same way for a projected, in-progress, or decided
    series.

    win_probability is always the SERIES winner's own probability (via
    season_simulation.series_win_probability), computed from a single
    game's Elo probability plus the real record so far -- not one game's
    raw probability. The single game's own probability comes from the
    next unplayed real game's own logged/live prediction when one exists
    (computing it on the spot on a cache miss, same as _resolve_matchup),
    falling back to a fresh Elo estimate when no real game is logged yet
    (series hasn't started, or ingest hasn't caught up to the next game)
    -- both cases go through the exact same formula, just with wins_a/
    wins_b at 0/0 or the real partial record respectively.

    Not-yet-final slots also carry predicted_wins_a/predicted_wins_b (via
    season_simulation.predicted_series_score) -- the single most likely
    FINAL record the series ends at, distinct from wins_a/wins_b's own
    current/live record (always 0-0 before the series starts). Omitted
    once "final", since the real record already answers the same
    question with no need for a prediction."""
    games = real_series.get(frozenset((team_a, team_b)), [])
    wins_a, wins_b = _series_record(team_a, team_b, games)

    if wins_a >= 4 or wins_b >= 4:
        return {
            "status": "final",
            "team_a": team_a, "team_b": team_b, "seed_a": seed_a, "seed_b": seed_b,
            "predicted_winner": None, "win_probability": None,
            "actual_winner": team_a if wins_a >= 4 else team_b,
            "wins_a": wins_a, "wins_b": wins_b,
        }

    next_game = next((g for g in games if g.get("status") != "completed"), None)
    game_probability_a = None
    if next_game is not None:
        event_key_value = next_game["event_key"]
        home_id, away_id = _home_and_away(next_game)
        logged = _logged_win_probability(predictions_table, event_key_value)
        if logged is None:
            try:
                event_prediction.compute_and_cache_event(storage, s3, predictions_table, next_game["event_id"])
                logged = _logged_win_probability(predictions_table, event_key_value)
            except Exception:
                logger.exception("Failed computing a live prediction for series game %s", event_key_value)
        if logged is not None:
            home_probability = logged["home_win_probability"]
            game_probability_a = home_probability if home_id == team_a else 1 - home_probability

    if game_probability_a is None:
        # No real next game logged yet (series hasn't started, or ingest
        # hasn't caught up) -- fall back to the model's own Elo estimate,
        # same input project_matchup itself would use.
        rating_a = current_ratings.get(team_a, season_simulation.DEFAULT_STARTING_RATING)
        rating_b = current_ratings.get(team_b, season_simulation.DEFAULT_STARTING_RATING)
        game_probability_a = season_simulation.expected_score(rating_a, rating_b, home_advantage)

    series_probability_a = season_simulation.series_win_probability(game_probability_a, wins_a, wins_b)
    predicted_winner = team_a if series_probability_a >= 0.5 else team_b
    win_probability = series_probability_a if predicted_winner == team_a else 1 - series_probability_a
    predicted_wins_a, predicted_wins_b = season_simulation.predicted_series_score(game_probability_a, wins_a, wins_b)

    return {
        "status": "scheduled" if games else "projected",
        "team_a": team_a, "team_b": team_b, "seed_a": seed_a, "seed_b": seed_b,
        "predicted_winner": predicted_winner, "win_probability": win_probability,
        "wins_a": wins_a, "wins_b": wins_b,
        "predicted_wins_a": predicted_wins_a, "predicted_wins_b": predicted_wins_b,
    }


def _project_series_round(
    round_name: str, pairs: list[tuple[str, str, int | None, int | None]],
    real_series: dict[frozenset, list[dict]], storage: FeatureStorage, s3, predictions_table,
    current_ratings: dict[str, float], home_advantage: float,
) -> tuple[dict, list[tuple[str, int | None]]]:
    """Best-of-7 sibling of _project_bracket_round -- resolves one round's
    worth of series slots via _resolve_series_matchup instead of a single
    game each."""
    matchups = []
    advancing = []
    for team_a, team_b, seed_a, seed_b in pairs:
        matchup = _resolve_series_matchup(
            team_a, team_b, seed_a, seed_b, real_series, storage, s3, predictions_table,
            current_ratings, home_advantage,
        )
        matchups.append(matchup)
        winner = matchup["predicted_winner"] if matchup["status"] != "final" else matchup["actual_winner"]
        winner_seed = seed_a if winner == team_a else seed_b
        advancing.append((winner, winner_seed))
    return {"round": round_name, "matchups": matchups}, advancing


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


def _project_conference_bracket_reconciled(
    direct_seeds: list[str], seed_7: str, seed_8: str, seed_9: str, seed_10: str,
    real_matchups: dict[frozenset, dict], real_series: dict[frozenset, list[dict]],
    storage: FeatureStorage, s3, predictions_table,
    current_ratings: dict[str, float], home_advantage: float,
) -> tuple[list[dict], str]:
    """Same two-stage shape as season_simulation.project_conference_bracket
    (Play-In then the fixed no-reseed 8-team bracket), but reconciled
    against real results: Play-In goes through _resolve_matchup's
    real-vs-projected reconciliation (both play-in games are single
    elimination, not a series); every round after it goes through
    _resolve_series_matchup instead (Conference Quarterfinals onward is
    best-of-7). Returns (rounds, champion)."""
    # Play-In is built game-by-game (unlike the other rounds), since
    # game 3's own participants depend on games 1/2's own results --
    # _project_bracket_round's own "resolve a static list of pairs" shape
    # doesn't fit a round with an internal dependency like that.
    game1 = _resolve_matchup(
        seed_7, seed_8, 7, 8, real_matchups, storage, s3, predictions_table, current_ratings, home_advantage,
    )
    game1_winner = game1["predicted_winner"] if game1["status"] != "final" else game1["actual_winner"]
    game1_loser = seed_8 if game1_winner == seed_7 else seed_7
    game1_loser_seed = 8 if game1_winner == seed_7 else 7

    game2 = _resolve_matchup(
        seed_9, seed_10, 9, 10, real_matchups, storage, s3, predictions_table, current_ratings, home_advantage,
    )
    game2_winner = game2["predicted_winner"] if game2["status"] != "final" else game2["actual_winner"]
    game2_winner_seed = 9 if game2_winner == seed_9 else 10

    game3 = _resolve_matchup(
        game1_loser, game2_winner, game1_loser_seed, game2_winner_seed, real_matchups, storage, s3,
        predictions_table, current_ratings, home_advantage,
    )
    final_8_seed = game3["predicted_winner"] if game3["status"] != "final" else game3["actual_winner"]

    # Two rounds, not one -- game3 isn't played in parallel with games
    # 1/2, it's built FROM their results (matches
    # season_simulation.project_conference_bracket's own shape).
    play_in_round1 = {"round": "Play-In", "matchups": [game2, game1]}
    play_in_round2 = {"round": "Play-In Elimination", "matchups": [game3]}
    full_seeds = direct_seeds + [game1_winner, final_8_seed]

    seed_number = {team_id: rank + 1 for rank, team_id in enumerate(full_seeds)}
    one, two, three, four, five, six, seven, eight = full_seeds

    first_round, first_round_advancing = _project_series_round(
        "Conference Quarterfinals",
        [
            (one, eight, seed_number[one], seed_number[eight]),
            (four, five, seed_number[four], seed_number[five]),
            (three, six, seed_number[three], seed_number[six]),
            (two, seven, seed_number[two], seed_number[seven]),
        ],
        real_series, storage, s3, predictions_table, current_ratings, home_advantage,
    )

    semifinals, semifinal_advancing = _project_series_round(
        "Conference Semifinals",
        [
            (first_round_advancing[0][0], first_round_advancing[1][0], first_round_advancing[0][1], first_round_advancing[1][1]),
            (first_round_advancing[2][0], first_round_advancing[3][0], first_round_advancing[2][1], first_round_advancing[3][1]),
        ],
        real_series, storage, s3, predictions_table, current_ratings, home_advantage,
    )

    championship, championship_advancing = _project_series_round(
        "Conference Finals",
        [(semifinal_advancing[0][0], semifinal_advancing[1][0], semifinal_advancing[0][1], semifinal_advancing[1][1])],
        real_series, storage, s3, predictions_table, current_ratings, home_advantage,
    )

    return [play_in_round1, play_in_round2, first_round, semifinals, championship], championship_advancing[0][0]


def _bracket_payload(
    storage: FeatureStorage, s3, predictions_table, season_inputs: dict, simulation: dict[str, dict],
) -> dict:
    """Builds the full NBA playoff bracket (both conferences, play-in
    included, plus the Finals), reconciled against real results as they
    exist right now (see _resolve_matchup for the 3-state design). Seeds
    from real wins/point-differential once the regular season is actually
    over (season_inputs["remaining_games"] empty), else from
    simulate_season's own projected_wins."""
    regular_season_over = not season_inputs["remaining_games"]
    if regular_season_over:
        wins, point_differential = season_inputs["wins"], season_inputs["point_differential"]
    else:
        wins = {team_id: projection["projected_wins"] for team_id, projection in simulation.items()}
        point_differential = season_inputs["point_differential"]

    conferences = season_simulation._teams_by_conference()
    current_season = season_inputs["current_season"]
    real_matchups = _real_postseason_matchups(storage, current_season)
    real_series = _real_postseason_series(storage, current_season)
    ratings = season_inputs["current_ratings"]
    home_advantage = season_simulation.DEFAULT_HOME_ADVANTAGE

    conference_results: dict[str, list[dict]] = {}
    champions: dict[str, str] = {}
    for conference, conference_teams in conferences.items():
        seeds = season_simulation._seed_conference(conference_teams, wins, point_differential)
        direct_seeds = seeds[:season_simulation.DIRECT_PLAYOFF_SEEDS]
        seed_7, seed_8, seed_9, seed_10 = seeds[6:season_simulation.PLAY_IN_FIELD_SIZE]
        rounds, champion = _project_conference_bracket_reconciled(
            direct_seeds, seed_7, seed_8, seed_9, seed_10, real_matchups, real_series, storage, s3,
            predictions_table, ratings, home_advantage,
        )
        conference_results[conference] = rounds
        champions[conference] = champion

    # NBA Finals is also best-of-7 (_resolve_series_matchup, not
    # _resolve_matchup) -- home-court goes to the better regular-season
    # record, point differential as tiebreak. Decided once here since a
    # series' host team stays fixed across all 7 possible games even
    # though which games it physically hosts alternates (2-2-1-1-1).
    (conference_a, champion_a), (conference_b, champion_b) = champions.items()
    record_a = (wins.get(champion_a, 0), point_differential.get(champion_a, 0))
    record_b = (wins.get(champion_b, 0), point_differential.get(champion_b, 0))
    finals_home, finals_away = (champion_a, champion_b) if record_a >= record_b else (champion_b, champion_a)
    finals_matchup = _resolve_series_matchup(
        finals_home, finals_away, None, None, real_series, storage, s3, predictions_table, ratings, home_advantage,
    )
    finals_winner = finals_matchup["predicted_winner"] if finals_matchup["status"] != "final" else finals_matchup["actual_winner"]

    bracket = {
        "conferences": conference_results,
        "finals": finals_matchup,
        "champion": finals_winner,
    }
    return enrich_bracket_team_names(storage, SPORT, bracket)


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
