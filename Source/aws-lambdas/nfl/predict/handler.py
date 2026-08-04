"""
NFL inference Lambda. Triggered by API Gateway (REST API, Lambda proxy
integration) behind the Cognito authorizer already wired in
Terraform/api-gateway.tf -- every request reaching this function has
already had its JWT validated by API Gateway itself; this code never
checks auth.

Routes (see Terraform/lambda-nfl-predict.tf for the API Gateway wiring):
    GET /nfl/events?status=scheduled|completed
        -> scoped to exactly one week, not the whole matching history:
           status=scheduled returns the soonest upcoming week (empty if
           that week hasn't been ingested yet -- the frontend shows a
           "coming soon" state rather than mistaking that for no games);
           status=completed returns the most recently completed week, each
           event carrying a `prediction_comparison` block (predicted vs.
           actual win/margin/score, `null` if no prediction was ever
           logged for that event before it was played -- see
           _prediction_comparison's own docstring for why this reads the
           predictions-table audit trail rather than recomputing one now).
           Every event also carries `round` -- the playoff round name
           (Wild Card/Divisional/Conference Championship/Super Bowl) for
           a postseason game, `null` for regular season -- see
           _round_label. Excludes the Pro Bowl and any other exhibition
           game entirely (see is_real_franchise_matchup).
    GET /nfl/predictions/events/{event_id}
        -> win probability, margin, home score, and away score for one
           upcoming/completed matchup, computed from one shared live
           feature vector (see live_features.build_live_event_features),
           plus a `leaders` block: presumptive passing/receiving/rushing/
           sacks leaders per team (see
           live_features.build_live_event_leader_candidates), each
           scored against the matching player-prop model. `leaders` is
           `null` if it couldn't be computed -- best-effort, never fails
           the core predictions above over it.
    GET /nfl/predictions/events/{event_id}/players/{entity_id}?stat=passing_yards
        -> one player-prop prediction for one player in one game. `stat`
           must be one of the trained TARGET_STAT values (see
           Terraform/scheduler-nfl-train-player-prop-model.tf's
           nfl_player_prop_stats).
    GET /nfl/season
        -> the current season's standings (real record so far + Elo Monte
           Carlo projected final wins, division-winner/playoff/
           championship probability per team -- see season_simulation.py)
           and, per tracked player-prop stat, a top-10 season-long
           leaderboard projected from each candidate's current total plus
           their own model's prediction for their team's next game.
           `leaderboards` is `null` if it couldn't be computed --
           best-effort, same reasoning as `leaders` above.

{event_id}/{entity_id} are raw ESPN ids (e.g. "401547417",
"3139477"), not the internal SPORT#NFL#EVENT#... key -- translated via
library.schema.keys the same way every other NFL adapter does.

Every prediction is computed live on every request (no caching, no
serving a previously-computed value back) and logged to the predictions
table for the audit trail -- see design/DATA_SCHEMA.md's Predictions
table. The predictions table's IAM grant is PutItem-only (see
Terraform/iam-lambda-inference.tf); this function never reads a past
prediction back.
"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
from datetime import datetime, timezone

from boto3.dynamodb.conditions import Key

from library.aws.dynamodb_table import DynamoDBTable
from library.aws.s3_manager import S3Manager
from library.features.nfl import compute_elo_ratings
from library.features.nfl_teams import is_real_franchise_matchup
from library.schema.keys import entity_key as build_entity_key
from library.schema.keys import event_key as build_event_key
from library.storage.feature_storage import FeatureStorage
from library.storage.model_artifacts import current_version_key, model_artifact_key
import live_features
import model_loader
import season_simulation

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nfl-predict")

SPORT = "nfl"
WIN_PROBABILITY_MODEL = "win-probability"
SCORE_MODELS = {"margin": "score-margin", "home_score": "home-score", "away_score": "away-score"}

# season_type=3 (postseason) week -> round name, confirmed live against
# ESPN's own scoreboard (season 2020: week 1=Wild Card, 2=Divisional,
# 3=Conference Championship, 4=Pro Bowl, 5=Super Bowl). Week 4 is
# deliberately absent -- that's always the Pro Bowl, already excluded
# entirely by is_real_franchise_matchup before this mapping is consulted,
# so a real week=4 postseason game should never occur. This numbering has
# been stable across this project's whole 2016-2025+ window -- the
# playoff field expanded to 7 teams per conference in 2020, but the
# round *count* and week numbering didn't change.
POSTSEASON_ROUND_LABELS = {1: "Wild Card", 2: "Divisional", 3: "Conference Championship", 5: "Super Bowl"}

# Source of truth is Terraform/scheduler-nfl-train-player-prop-model.tf's
# nfl_player_prop_stats map -- duplicated here as a plain list (not read
# from Terraform at runtime) since there's no DynamoDB-backed model
# registry yet (design/PROJECT_PLAN.md Phase 4) to read it from instead.
PLAYER_PROP_STATS = [
    "passing_yards", "passing_touchdowns", "rushing_yards", "rushing_touchdowns",
    "receiving_yards", "receiving_touchdowns", "defensive_sacks",
]

# Which player-prop stat(s) each leader category needs scored -- passing
# and receiving/rushing candidates each need two related stats (e.g. a
# QB's yards AND touchdowns), sacks needs just the one.
LEADER_CATEGORY_STATS = {
    "passing": ["passing_yards", "passing_touchdowns"],
    "receiving": ["receiving_yards", "receiving_touchdowns"],
    "rushing": ["rushing_yards", "rushing_touchdowns"],
    "sacks": ["defensive_sacks"],
}

_CORS_HEADERS = {
    # Wildcard is safe here specifically because auth is a bearer JWT in
    # the Authorization header (validated by the Cognito authorizer
    # before this function ever runs), not a cookie -- there's no
    # cookie-based session for a permissive CORS origin to leak.
    "Access-Control-Allow-Origin": "*",
    "Content-Type": "application/json",
}

# Initialized once per container lifetime, reused across warm invocations
# -- same lazy-singleton pattern as normalize/handler.py's _get_storage().
_storage: FeatureStorage | None = None
_model_bucket: S3Manager | None = None
_predictions_table: DynamoDBTable | None = None


def _get_storage() -> FeatureStorage:
    global _storage
    if _storage is None:
        _storage = FeatureStorage()
    return _storage


def _get_model_bucket() -> S3Manager:
    global _model_bucket
    if _model_bucket is None:
        _model_bucket = S3Manager(os.environ["MODEL_ARTIFACTS_BUCKET_NAME"], region=os.environ.get("AWS_REGION"))
    return _model_bucket


def _get_predictions_table() -> DynamoDBTable:
    global _predictions_table
    if _predictions_table is None:
        _predictions_table = DynamoDBTable(os.environ["PREDICTIONS_TABLE_NAME"], region=os.environ.get("AWS_REGION"))
    return _predictions_table


def _response(status_code: int, body: dict) -> dict:
    return {"statusCode": status_code, "headers": _CORS_HEADERS, "body": json.dumps(body)}


def _model_name_to_prop(target_stat: str) -> str:
    return f"player-prop-{target_stat.replace('_', '-')}"


def _record_prediction(event_key_value: str, model_key: str, value) -> None:
    _get_predictions_table().put_item({
        "event_key": event_key_value,
        "model_key": model_key,
        "predicted_value": value,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })


def _get_cached_model(model_cache: dict, s3, model_name: str):
    """Several leader candidates within the same request often need the
    SAME model (e.g. every receiver candidate needs
    player-prop-receiving-yards) -- this loads each distinct model at
    most once per request instead of once per candidate."""
    if model_name not in model_cache:
        model_cache[model_name] = model_loader.load_current_model(s3, SPORT, model_name)
    return model_cache[model_name]


def _score_leader_candidate(storage, s3, model_cache: dict, feature_row: dict, stats: list[str]) -> dict:
    entity_id = feature_row["entity_id"]
    entity = storage.get_entity(SPORT, entity_id)
    result = {"entity_id": entity_id}
    if entity and entity.get("name"):
        result["name"] = entity["name"]

    for stat in stats:
        model_name = _model_name_to_prop(stat)
        try:
            booster, model_card = _get_cached_model(model_cache, s3, model_name)
        except model_loader.NoPromotedModelError:
            # This one stat's model hasn't been promoted yet -- leave it
            # out of the candidate's result rather than failing the whole
            # event prediction over a gap in an enhancement field.
            continue
        result[stat] = model_loader.predict(booster, model_card, feature_row)
    return result


def _predict_event_leaders(storage, s3, event_key_value: str) -> dict | None:
    """The `leaders` block -- passing/receiving/rushing/sacks leaders per
    team. Best-effort: any failure here is logged and swallowed rather
    than propagated, since the core win/margin/score predictions in
    _predict_event already succeeded by the time this runs and shouldn't
    be thrown away over a problem in this purely additive field."""
    try:
        candidates = live_features.build_live_event_leader_candidates(storage, SPORT, event_key_value)
    except Exception:
        logger.exception("Failed to build leader candidates for %s", event_key_value)
        return None

    model_cache: dict = {}

    def team_leaders(team_candidates: dict) -> dict:
        passing = team_candidates["passing"]
        return {
            "passing": _score_leader_candidate(
                storage, s3, model_cache, passing[0], LEADER_CATEGORY_STATS["passing"],
            ) if passing else None,
            "receiving": [
                _score_leader_candidate(storage, s3, model_cache, row, LEADER_CATEGORY_STATS["receiving"])
                for row in team_candidates["receiving"]
            ],
            "rushing": [
                _score_leader_candidate(storage, s3, model_cache, row, LEADER_CATEGORY_STATS["rushing"])
                for row in team_candidates["rushing"]
            ],
            "sacks": [
                _score_leader_candidate(storage, s3, model_cache, row, LEADER_CATEGORY_STATS["sacks"])
                for row in team_candidates["sacks"]
            ],
        }

    return {"home": team_leaders(candidates["home"]), "away": team_leaders(candidates["away"])}


def _predict_event(event_id: str) -> dict:
    storage = _get_storage()
    s3 = _get_model_bucket()
    event_key_value = build_event_key(SPORT, event_id)
    feature_row = live_features.build_live_event_features(storage, SPORT, event_key_value)

    booster, model_card = model_loader.load_current_model(s3, SPORT, WIN_PROBABILITY_MODEL)
    home_win_probability = model_loader.predict(booster, model_card, feature_row)
    predictions = {
        "win_probability": {"home_win_probability": home_win_probability, "model_version": model_card["version"]},
    }
    _record_prediction(
        event_key_value, f"MODEL#{WIN_PROBABILITY_MODEL}#v{model_card['version']}", predictions["win_probability"],
    )

    for target, model_name in SCORE_MODELS.items():
        booster, model_card = model_loader.load_current_model(s3, SPORT, model_name)
        value = model_loader.predict(booster, model_card, feature_row)
        predictions[target] = {"value": value, "model_version": model_card["version"]}
        _record_prediction(event_key_value, f"MODEL#{model_name}#v{model_card['version']}", predictions[target])

    return {
        "event_key": event_key_value,
        "predictions": predictions,
        "leaders": _predict_event_leaders(storage, s3, event_key_value),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _predict_player_prop(event_id: str, entity_id: str, target_stat: str) -> dict:
    storage = _get_storage()
    s3 = _get_model_bucket()
    event_key_value = build_event_key(SPORT, event_id)
    feature_row = live_features.build_live_player_features(storage, SPORT, event_key_value, entity_id)

    model_name = _model_name_to_prop(target_stat)
    booster, model_card = model_loader.load_current_model(s3, SPORT, model_name)
    value = model_loader.predict(booster, model_card, feature_row)

    entity_key_value = build_entity_key(SPORT, entity_id)
    _record_prediction(
        event_key_value, f"MODEL#{model_name}#v{model_card['version']}#PLAYER#{entity_id}", {"value": value},
    )

    return {
        "event_key": event_key_value,
        "entity_key": entity_key_value,
        "stat": target_stat,
        "prediction": {"value": value, "model_version": model_card["version"]},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _week_key(event: dict) -> tuple:
    return (event.get("season"), event.get("season_type"), event.get("week"))


def _previous_week_events(completed: list[dict]) -> list[dict]:
    """Only the most recently completed week's games -- not the full
    history. "Most recent" is the (season, season_type, week) of whichever
    completed event has the latest event_date; every other game sharing
    that same triple is part of the same week."""
    if not completed:
        return []
    latest = max(completed, key=lambda e: e.get("event_date", ""))
    target = _week_key(latest)
    return [e for e in completed if _week_key(e) == target]


def _next_week_events(scheduled: list[dict]) -> list[dict]:
    """Only the soonest upcoming week's games. Empty if nothing's been
    ingested yet for the next week (e.g. between seasons, before the
    Tuesday/Wednesday ingest schedule has run for it) -- the frontend
    shows a "coming soon" state for that case instead of an empty list
    that looks like a data problem."""
    if not scheduled:
        return []
    earliest = min(scheduled, key=lambda e: e.get("event_date", ""))
    target = _week_key(earliest)
    return [e for e in scheduled if _week_key(e) == target]


def _actual_result(event: dict) -> dict | None:
    home_away = _home_and_away(event)
    if home_away is None:
        return None
    home_id, away_id = home_away
    participants = event.get("participants", [])
    home = next((p for p in participants if p.get("entity_id") == home_id), None)
    away = next((p for p in participants if p.get("entity_id") == away_id), None)
    home_score = (home.get("result") or {}).get("score") if home else None
    away_score = (away.get("result") or {}).get("score") if away else None
    if home_score is None or away_score is None:
        return None
    return {"home_score": home_score, "away_score": away_score, "home_won": home_score > away_score}


def _prediction_comparison(event: dict) -> dict | None:
    """Compares this event's logged prediction against the actual result --
    reads the audit trail _record_prediction already writes (whatever was
    predicted BEFORE the game), never recomputes one now. Recomputing after
    the fact would build live features from rolling averages/Elo that may
    already include this very game's own now-normalized stats, leaking the
    outcome into its own "prediction". Returns None if no prediction was
    ever logged for this event (nobody requested one before it was played)
    or the event has no final score yet."""
    actual = _actual_result(event)
    if actual is None:
        return None

    rows = _get_predictions_table().query(Key("event_key").eq(event["event_key"]))

    def _row_for(model_prefix: str) -> dict | None:
        return next((r for r in rows if r["model_key"].startswith(f"MODEL#{model_prefix}#")), None)

    win_probability_row = _row_for(WIN_PROBABILITY_MODEL)
    if win_probability_row is None:
        return None

    margin_row = _row_for(SCORE_MODELS["margin"])
    home_score_row = _row_for(SCORE_MODELS["home_score"])
    away_score_row = _row_for(SCORE_MODELS["away_score"])

    home_win_probability = win_probability_row["predicted_value"]["home_win_probability"]
    predicted_home_won = home_win_probability >= 0.5

    return {
        "predicted_home_win_probability": home_win_probability,
        "predicted_home_won": predicted_home_won,
        "actual_home_won": actual["home_won"],
        "correct": predicted_home_won == actual["home_won"],
        "predicted_margin": margin_row["predicted_value"]["value"] if margin_row else None,
        "actual_margin": actual["home_score"] - actual["away_score"],
        "predicted_home_score": home_score_row["predicted_value"]["value"] if home_score_row else None,
        "predicted_away_score": away_score_row["predicted_value"]["value"] if away_score_row else None,
        "actual_home_score": actual["home_score"],
        "actual_away_score": actual["away_score"],
    }


def _round_label(event: dict) -> str | None:
    """None for regular season (season_type=2) or any postseason week not
    in POSTSEASON_ROUND_LABELS -- a raw week number means nothing for the
    postseason (nobody thinks of the Super Bowl as "week 5"), but is
    exactly what a regular-season game should keep showing."""
    if event.get("season_type") != 3:
        return None
    return POSTSEASON_ROUND_LABELS.get(event.get("week"))


def _list_events(status: str) -> dict:
    storage = _get_storage()
    # Excludes the Pro Bowl and any other exhibition game -- see
    # is_real_franchise_matchup's own docstring.
    events = [e for e in storage.get_all_events(SPORT, status=status) if is_real_franchise_matchup(e)]

    if status == "completed":
        events = _previous_week_events(events)
    elif status == "scheduled":
        events = _next_week_events(events)

    def _entry(e: dict) -> dict:
        entry = {
            "event_id": e["event_id"],
            "event_date": e.get("event_date"),
            "status": e.get("status"),
            "season": e.get("season"),
            "season_type": e.get("season_type"),
            "week": e.get("week"),
            "round": _round_label(e),
            "participants": e.get("participants"),
        }
        if status == "completed":
            entry["prediction_comparison"] = _prediction_comparison(e)
        return entry

    return {"sport": SPORT, "events": [_entry(e) for e in events]}


def _load_model_summary(s3, model_name: str) -> dict | None:
    """One model's card summary, or None if it's never had a version
    promoted. 3 sequential S3 round-trips (existence check, pointer, card)
    -- factored out so _list_models can run every model's lookup
    concurrently instead of one full round-trip chain after another. Each
    model's lookup is fully independent of every other's."""
    pointer_key = current_version_key(SPORT, model_name)
    if not s3.object_exists(pointer_key):
        return None
    version = s3.get_json(pointer_key)["version"]
    card = s3.get_json(model_artifact_key(SPORT, model_name, version, "model_card.json"))
    top_features = [
        {"feature": name, "importance": value}
        for name, value in list(card.get("feature_importances", {}).items())[:5]
    ]
    return {
        "model_name": card["model_name"],
        "algorithm": card["algorithm"],
        "version": card["version"],
        "trained_at": card["trained_at"],
        **{k: v for k, v in card.items() if k in ("accuracy", "log_loss", "rmse", "mae", "naive_baseline_rmse", "naive_baseline_mae")},
        "top_features": top_features,
    }


def _list_models() -> dict:
    s3 = _get_model_bucket()
    prefix = f"{SPORT}/"
    model_names = sorted({key[len(prefix):].split("/")[0] for key in s3.list_keys(prefix)})

    if not model_names:
        return {"sport": SPORT, "models": []}

    # boto3 clients are thread-safe for concurrent calls -- sharing one S3
    # client across these threads is the documented, supported usage, not
    # a race. Previously this was N models * 3 sequential S3 round-trips
    # in series (confirmed live: ~2.9s for ~10 models); running each
    # model's own 3-call chain concurrently instead cuts wall-clock time
    # to roughly the slowest single model's chain, not the sum of all of
    # them.
    with ThreadPoolExecutor(max_workers=min(len(model_names), 10)) as executor:
        results = executor.map(lambda name: _load_model_summary(s3, name), model_names)

    return {"sport": SPORT, "models": [card for card in results if card is not None]}


def _home_and_away(event: dict) -> tuple[str, str] | None:
    """Same lookup as live_features._home_away_ids, but returns None on a
    malformed event instead of raising -- season-wide aggregation walks
    hundreds of events and should skip one bad row, not abort the whole
    projection the way a single-event lookup correctly does."""
    participants = event.get("participants", [])
    home = next((p for p in participants if p.get("role") == "home"), None)
    away = next((p for p in participants if p.get("role") == "away"), None)
    if home is None or away is None:
        return None
    return home["entity_id"], away["entity_id"]


def _season_standings_inputs(storage: FeatureStorage) -> dict:
    """Fetches this season's completed+scheduled events once and derives
    everything season_simulation.simulate_season needs, plus each team's
    next scheduled event_key (reused by _leaderboards below)."""
    # Excludes the Pro Bowl and any other exhibition matchup -- its AFC/NFC
    # all-star "teams" aren't real franchises (see
    # library.features.nfl_teams.is_real_franchise_matchup), so a played
    # one would otherwise count as a real win/loss and Elo update for a
    # non-existent team_id.
    scheduled = [e for e in storage.get_all_events(SPORT, status="scheduled") if is_real_franchise_matchup(e)]
    completed = [e for e in storage.get_all_events(SPORT, status="completed") if is_real_franchise_matchup(e)]
    current_season = max(
        (e.get("season") for e in scheduled + completed if e.get("season") is not None), default=None,
    )
    scheduled = [e for e in scheduled if e.get("season") == current_season]
    completed = [e for e in completed if e.get("season") == current_season]

    wins: dict[str, int] = {}
    losses: dict[str, int] = {}
    point_differential: dict[str, int] = {}
    team_last_completed_date: dict[str, str] = {}
    for event in completed:
        home_away = _home_and_away(event)
        if home_away is None:
            continue
        for entity_id, opponent_id in (home_away, home_away[::-1]):
            participant = next(p for p in event["participants"] if p.get("entity_id") == entity_id)
            opponent = next(p for p in event["participants"] if p.get("entity_id") == opponent_id)
            score = (participant.get("result") or {}).get("score")
            opponent_score = (opponent.get("result") or {}).get("score")
            if score is None or opponent_score is None:
                continue
            wins[entity_id] = wins.get(entity_id, 0) + (1 if score > opponent_score else 0)
            losses[entity_id] = losses.get(entity_id, 0) + (1 if score < opponent_score else 0)
            point_differential[entity_id] = point_differential.get(entity_id, 0) + (score - opponent_score)
            event_date = event.get("event_date", "")
            if event_date > team_last_completed_date.get(entity_id, ""):
                team_last_completed_date[entity_id] = event_date

    _, current_ratings = compute_elo_ratings(completed)

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

    return {
        "current_season": current_season,
        "completed_event_keys": {e["event_key"] for e in completed},
        "wins": wins,
        "losses": losses,
        "point_differential": point_differential,
        "current_ratings": current_ratings,
        "remaining_games": remaining_games,
        "team_next_event": team_next_event,
        "team_last_completed_date": team_last_completed_date,
        "games_remaining": Counter(team_id for pair in remaining_games for team_id in pair),
    }


def _leaderboards(storage: FeatureStorage, s3, model_cache: dict, season_inputs: dict) -> dict:
    """Top-10 season-long leaderboard per tracked player-prop stat,
    projected as current season-to-date total + (their own model's
    prediction for their team's NEXT scheduled game * games remaining) --
    see season_simulation.project_leaderboard's own docstring for why
    this is a flat estimate rather than a per-opponent simulation."""
    season_player_stats = [
        row for row in storage.get_all_player_game_stats()
        if row.get("event_key") in season_inputs["completed_event_keys"]
    ]

    player_team: dict[str, str] = {}
    for row in season_player_stats:
        player_team.setdefault(row["entity_id"], row.get("team_id"))

    leaderboards: dict[str, list[dict]] = {}
    for stat in PLAYER_PROP_STATS:
        current_totals: dict[str, float] = {}
        for row in season_player_stats:
            value = row.get("stat_line", {}).get(stat)
            if value is not None:
                entity_id = row["entity_id"]
                current_totals[entity_id] = current_totals.get(entity_id, 0) + value

        model_name = _model_name_to_prop(stat)
        per_game_projections: dict[str, float] = {}
        try:
            booster, model_card = _get_cached_model(model_cache, s3, model_name)
        except model_loader.NoPromotedModelError:
            booster = None

        # Loading the model once above (single-threaded, before fan-out)
        # avoids a check-then-set race in _get_cached_model's dict if
        # candidates for the same stat resolved it concurrently instead.
        # current_ratings/team_last_event_dates reuse what
        # _season_standings_inputs already computed once for the whole
        # request -- without them, build_live_player_features would redo
        # a full-history Elo recompute per candidate per stat, which is
        # what actually blew this route's 29s budget (confirmed live via
        # CloudWatch: every /nfl/season request here was timing out).
        if booster is not None:
            def _project(entity_id: str) -> tuple[str, float] | None:
                next_event_key = season_inputs["team_next_event"].get(player_team.get(entity_id))
                if next_event_key is None:
                    return None
                try:
                    feature_row = live_features.build_live_player_features(
                        storage, SPORT, next_event_key, entity_id,
                        current_ratings=season_inputs["current_ratings"],
                        team_last_event_dates=season_inputs["team_last_completed_date"],
                    )
                    return entity_id, model_loader.predict(booster, model_card, feature_row)
                except live_features.EventNotFoundError:
                    return None
                except Exception:
                    logger.exception("Failed to project %s for %s", stat, entity_id)
                    return None

            with ThreadPoolExecutor(max_workers=max(1, min(len(current_totals), 10))) as executor:
                for result in executor.map(_project, current_totals):
                    if result is not None:
                        entity_id, value = result
                        per_game_projections[entity_id] = value

        games_remaining = {
            entity_id: season_inputs["games_remaining"].get(player_team.get(entity_id), 0)
            for entity_id in current_totals
        }

        top = season_simulation.project_leaderboard(current_totals, per_game_projections, games_remaining, top_n=10)
        for row in top:
            entity = storage.get_entity(SPORT, row["entity_id"])
            if entity and entity.get("name"):
                row["name"] = entity["name"]
        leaderboards[stat] = top

    return leaderboards


def _season_projection() -> dict:
    storage = _get_storage()
    s3 = _get_model_bucket()
    model_cache: dict = {}

    season_inputs = _season_standings_inputs(storage)
    simulation = season_simulation.simulate_season(
        season_inputs["wins"], season_inputs["losses"], season_inputs["point_differential"],
        season_inputs["remaining_games"], season_inputs["current_ratings"],
    )

    standings = sorted(
        (
            {
                "team_id": team_id,
                "wins": season_inputs["wins"].get(team_id, 0),
                "losses": season_inputs["losses"].get(team_id, 0),
                **projection,
            }
            for team_id, projection in simulation.items()
        ),
        key=lambda row: row["projected_wins"],
        reverse=True,
    )

    try:
        leaderboards = _leaderboards(storage, s3, model_cache, season_inputs)
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


def lambda_handler(event, context):
    path_params = event.get("pathParameters") or {}
    query_params = event.get("queryStringParameters") or {}
    resource = event.get("resource", "")

    try:
        if resource == "/nfl/events":
            return _response(200, _list_events(query_params.get("status", "scheduled")))

        if resource == "/nfl/models":
            return _response(200, _list_models())

        if resource == "/nfl/season":
            return _response(200, _season_projection())

        if resource == "/nfl/predictions/events/{event_id}/players/{entity_id}":
            target_stat = query_params.get("stat")
            if not target_stat:
                return _response(400, {"error": "Missing required query parameter: stat"})
            body = _predict_player_prop(path_params["event_id"], path_params["entity_id"], target_stat)
            return _response(200, body)

        if resource == "/nfl/predictions/events/{event_id}":
            body = _predict_event(path_params["event_id"])
            return _response(200, body)

        return _response(404, {"error": f"No route for resource {resource!r}"})

    except live_features.EventNotFoundError as exc:
        return _response(404, {"error": str(exc)})
    except live_features.MalformedEventError as exc:
        return _response(422, {"error": str(exc)})
    except model_loader.NoPromotedModelError as exc:
        return _response(503, {"error": str(exc)})
    except Exception:
        logger.exception("Unhandled error serving %s", resource)
        return _response(500, {"error": "Internal server error"})
