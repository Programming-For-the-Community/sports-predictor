"""
Read-only NFL serving logic -- GET /nfl/events and GET /nfl/models --
shared between the heavy inference Lambda (predictions/season,
Source/aws-lambdas/nfl/predict) and the light read-only Lambda
(Source/aws-lambdas/nfl/predict-read). Neither route ever loads or
deserializes an ML model artifact (list_models only reads a model card's
JSON metadata; list_events only reads events + already-logged predictions
from DynamoDB) -- moved here from predict/handler.py specifically so a
Lambda serving ONLY these two routes never has to import xgboost/
scikit-learn/pandas at all. Those two routes are also this API's most
frequently hit traffic (every page load), so cutting their cold start
matters more than the less-frequent, inherently-heavier prediction routes.

Callers own their own storage/s3/predictions_table objects and Lambda-
lifecycle concerns (lazy singletons, env var lookups) -- this module is
pure request-shaping logic, the same boundary library.ml.backtest draws
around training orchestration vs. algorithm specifics.
"""
import re
from concurrent.futures import ThreadPoolExecutor

from boto3.dynamodb.conditions import Key

from library.features.nfl_teams import is_real_franchise_matchup
from library.storage.model_artifacts import current_version_key, model_artifact_key
from library.storage.season_projections import season_projection_key

WIN_PROBABILITY_MODEL = "win-probability"
SCORE_MODELS = {"margin": "score-margin", "home_score": "home-score", "away_score": "away-score"}

# Mirrors predict/handler.py's LEADER_CATEGORY_STATS, inverted (stat ->
# category instead of category -> stats) -- duplicated rather than
# imported since predict-read never imports predict/handler.py (that's
# the whole point of this module's own split, see its own docstring).
_STAT_CATEGORY = {
    "passing_yards": "passing", "passing_touchdowns": "passing",
    "receiving_yards": "receiving", "receiving_touchdowns": "receiving",
    "rushing_yards": "rushing", "rushing_touchdowns": "rushing",
    "defensive_sacks": "sacks",
}

# Matches predict/handler.py's _record_prediction model_key shape for a
# player-prop prediction (MODEL#player-prop-passing-yards#v3#PLAYER#qb1)
# -- written both by a manually-queried single player+stat
# (_predict_player_prop) and, as of the leaders-comparison feature, by
# every scored leader candidate (_score_leader_candidate) -- both are
# interchangeable here, this doesn't care which wrote a given row.
_PLAYER_PROP_MODEL_KEY_RE = re.compile(r"^MODEL#player-prop-([a-z-]+)#v\d+#PLAYER#(.+)$")

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


def _prediction_comparison(predictions_table, event: dict) -> dict | None:
    """Compares this event's logged prediction against the actual result --
    reads the audit trail predict/handler.py's _record_prediction already
    wrote (whatever was predicted BEFORE the game), never recomputes one
    now. Recomputing after the fact would build live features from rolling
    averages/Elo that may already include this very game's own
    now-normalized stats, leaking the outcome into its own "prediction".
    Returns None if no prediction was ever logged for this event (nobody
    requested one before it was played) or the event has no final score
    yet."""
    actual = _actual_result(event)
    if actual is None:
        return None

    rows = predictions_table.query(Key("event_key").eq(event["event_key"]))

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


def _leaders_comparison(storage, predictions_table, sport: str, event: dict) -> dict | None:
    """Player-prop predicted-vs-actual for a completed event -- the
    player-level counterpart to _prediction_comparison, same "read the
    audit trail, never recompute" reasoning: recomputing live features for
    an already-played game would leak that game's own now-normalized
    result into its own "prediction" (see event_detail_page.dart's own
    comment on the frontend side of this same rule). None if no leader/
    player-prop prediction was ever recorded for this event -- nobody
    viewed its detail page while it was still scheduled, same honest gap
    _prediction_comparison already has for the team-level comparison, not
    a bug.

    Shape mirrors the (predicted-only) `leaders` block
    predict/handler.py's _predict_event_leaders returns: `passing` is a
    single entry or null per team (only one passing candidate is ever
    scored), `receiving`/`rushing`/`sacks` are lists."""
    home_away = _home_and_away(event)
    if home_away is None:
        return None
    home_id, away_id = home_away

    rows = predictions_table.query(Key("event_key").eq(event["event_key"]))
    predicted_by_entity: dict[str, dict[str, float]] = {}
    for row in rows:
        match = _PLAYER_PROP_MODEL_KEY_RE.match(row["model_key"])
        if match is None:
            continue
        stat = match.group(1).replace("-", "_")
        entity_id = match.group(2)
        predicted_by_entity.setdefault(entity_id, {})[stat] = row["predicted_value"]["value"]

    if not predicted_by_entity:
        return None

    actual_by_entity = {
        row["entity_id"]: row.get("stat_line", {})
        for row in storage.get_player_game_stats_for_event(event["event_key"])
    }

    home: dict[str, list[dict] | dict | None] = {"passing": None, "receiving": [], "rushing": [], "sacks": []}
    away: dict[str, list[dict] | dict | None] = {"passing": None, "receiving": [], "rushing": [], "sacks": []}

    for entity_id, predicted_stats in predicted_by_entity.items():
        category = next((_STAT_CATEGORY[stat] for stat in predicted_stats if stat in _STAT_CATEGORY), None)
        if category is None:
            continue
        entity = storage.get_entity(sport, entity_id)
        team_id = (entity.get("metadata") or {}).get("team_id") if entity else None
        if team_id == home_id:
            bucket = home
        elif team_id == away_id:
            bucket = away
        else:
            # Traded/waived since the prediction was recorded, or a
            # lookup failure -- skip rather than guess which side.
            continue

        actual_stats = actual_by_entity.get(entity_id, {})
        entry = {
            "entity_id": entity_id,
            "predicted": predicted_stats,
            "actual": {stat: actual_stats[stat] for stat in predicted_stats if stat in actual_stats},
        }
        if entity and entity.get("name"):
            entry["name"] = entity["name"]

        if category == "passing":
            bucket["passing"] = entry
        else:
            bucket[category].append(entry)

    return {"home": home, "away": away}


def _round_label(event: dict) -> str | None:
    """None for regular season (season_type=2) or any postseason week not
    in POSTSEASON_ROUND_LABELS -- a raw week number means nothing for the
    postseason (nobody thinks of the Super Bowl as "week 5"), but is
    exactly what a regular-season game should keep showing."""
    if event.get("season_type") != 3:
        return None
    return POSTSEASON_ROUND_LABELS.get(event.get("week"))


def list_events(storage, predictions_table, sport: str, status: str) -> dict:
    """GET /nfl/events?status=scheduled|completed -- scoped to exactly one
    week, not the whole matching history: status=scheduled returns the
    soonest upcoming week (empty if that week hasn't been ingested yet --
    the frontend shows a "coming soon" state rather than mistaking that
    for no games); status=completed returns the most recently completed
    week, each event carrying a `prediction_comparison` block (predicted
    vs. actual win/margin/score, `null` if no prediction was ever logged
    for that event before it was played -- see _prediction_comparison's
    own docstring for why this reads the predictions-table audit trail
    rather than recomputing one now). Every event also carries `round` --
    the playoff round name (Wild Card/Divisional/Conference Championship/
    Super Bowl) for a postseason game, `null` for regular season -- see
    _round_label. A completed event also carries `leaders_comparison` --
    player-prop predicted-vs-actual, `null` under the same "nobody
    recorded one before the game" condition as `prediction_comparison`,
    see _leaders_comparison. Excludes the Pro Bowl and any other
    exhibition game entirely (see is_real_franchise_matchup)."""
    events = [e for e in storage.get_all_events(sport, status=status) if is_real_franchise_matchup(e)]

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
            entry["prediction_comparison"] = _prediction_comparison(predictions_table, e)
            entry["leaders_comparison"] = _leaders_comparison(storage, predictions_table, sport, e)
        return entry

    return {"sport": sport, "events": [_entry(e) for e in events]}


def _load_model_summary(s3, sport: str, model_name: str) -> dict | None:
    """One model's card summary, or None if it's never had a version
    promoted. 3 sequential S3 round-trips (existence check, pointer, card)
    -- factored out so list_models can run every model's lookup
    concurrently instead of one full round-trip chain after another. Each
    model's lookup is fully independent of every other's."""
    pointer_key = current_version_key(sport, model_name)
    if not s3.object_exists(pointer_key):
        return None
    version = s3.get_json(pointer_key)["version"]
    card = s3.get_json(model_artifact_key(sport, model_name, version, "model_card.json"))
    top_features = [
        {"feature": name, "importance": value}
        for name, value in list(card.get("feature_importances", {}).items())[:5]
    ]
    return {
        "model_name": card["model_name"],
        "algorithm": card["algorithm"],
        "version": card["version"],
        "trained_at": card["trained_at"],
        **{k: v for k, v in card.items() if k in (
            "accuracy", "log_loss", "naive_baseline_accuracy", "rmse", "mae", "naive_baseline_rmse", "naive_baseline_mae",
        )},
        "top_features": top_features,
        # Every algorithm library.ml.backtest.run_backtest tried for this
        # target this run -- absent on a model card trained before the
        # backtesting harness existed. Each candidate carries "score" (the
        # same human-readable metric as this card's own top-level
        # accuracy/mae) and "rank_score" (the value of candidates_ranked_by
        # -- what actually decided the ranking, since the two can disagree
        # in direction -- see run_backtest's own comment on why both exist).
        "candidates": card.get("candidates"),
        "candidates_ranked_by": card.get("candidates_ranked_by"),
    }


def list_models(s3, sport: str) -> dict:
    """GET /nfl/models -- lists every currently-promoted model, with its
    latest model card summary. A model that's never had a version
    promoted simply doesn't appear in this list."""
    prefix = f"{sport}/"
    model_names = sorted({key[len(prefix):].split("/")[0] for key in s3.list_keys(prefix)})

    if not model_names:
        return {"sport": sport, "models": []}

    # boto3 clients are thread-safe for concurrent calls -- sharing one S3
    # client across these threads is the documented, supported usage, not
    # a race. Running each model's own 3-call chain concurrently cuts
    # wall-clock time to roughly the slowest single model's chain, not the
    # sum of all of them.
    with ThreadPoolExecutor(max_workers=min(len(model_names), 10)) as executor:
        results = executor.map(lambda name: _load_model_summary(s3, sport, name), model_names)

    return {"sport": sport, "models": [card for card in results if card is not None]}


def get_season_projection(s3, sport: str) -> dict | None:
    """GET /nfl/season -- reads the standings + leaderboard projection
    written weekly by the scheduled compute path (predict/handler.py's
    ScheduledSeasonProjection branch), never computed live here. None if
    the schedule hasn't fired yet (e.g. right after a fresh deploy) --
    the caller is expected to surface that as "not yet available" rather
    than treat it like a real 500."""
    key = season_projection_key(sport)
    if not s3.object_exists(key):
        return None
    return s3.get_json(key)
