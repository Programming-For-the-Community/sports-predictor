"""
Read-only NCAAFB serving logic -- GET /ncaafb/events, GET /ncaafb/models,
and GET /ncaafb/season -- shared between the heavy inference Lambda
(Source/aws-lambdas/ncaafb/predict) and the light read-only Lambda
(Source/aws-lambdas/ncaafb/predict-read). Same split, and same reasoning,
as library.serving.nfl_reads (see its own docstring) -- NOT a port of it,
duplicated deliberately so this Lambda never has to import that
NFL-named module.

Callers own their own storage/s3/predictions_table objects and Lambda-
lifecycle concerns, same boundary nfl_reads.py draws.
"""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from boto3.dynamodb.conditions import Key

from library.features.ncaafb import is_bowl_game, is_playoff_game
from library.serving.common import enrich_participants
from library.storage.model_artifacts import current_version_key, model_artifact_key
from library.storage.season_projections import season_projection_key

WIN_PROBABILITY_MODEL = "win-probability"
SCORE_MODELS = {"margin": "score-margin", "home_score": "home-score", "away_score": "away-score"}

# Mirrors predict/event_prediction.py's own LEADER_CATEGORY_STATS,
# inverted (stat -> category instead of category -> stats) -- duplicated
# rather than imported, same reasoning nfl_reads.py's own _STAT_CATEGORY
# gives. defensive_sacks is deliberately absent -- no sacks leader exists
# for NCAAFB (see predict/live_features.py's own docstring), so a
# defensive_sacks player-prop prediction (still requestable directly by
# entity_id) never shows up in the leaders_comparison block below.
_STAT_CATEGORY = {
    "passing_yards": "passing", "passing_touchdowns": "passing",
    "receiving_yards": "receiving", "receiving_touchdowns": "receiving",
    "rushing_yards": "rushing", "rushing_touchdowns": "rushing",
    "defensive_sacks": "sacks",
}

# Matches predict/event_prediction.py's record_prediction model_key shape
# for a player-prop prediction (MODEL#player-prop-passing-yards#v3#PLAYER#qb1).
_PLAYER_PROP_MODEL_KEY_RE = re.compile(r"^MODEL#player-prop-([a-z-]+)#v\d+#PLAYER#(.+)$")

# Mirrors predict/event_prediction.py's own LEADER_CATEGORY_STATS primary
# stat per category -- duplicated for the same reason _STAT_CATEGORY above
# is. The audit trail can hold more rows per category than this many
# (e.g. an event predicted before live_features.py's candidate-count cap
# changed), so this re-sorts and re-slices rather than trusting it already
# matches the leaders block shown pre-game.
_LEADER_CATEGORY_LIMITS = {"rushing": 2, "receiving": 3, "sacks": 3}
_CATEGORY_PRIMARY_STAT = {"rushing": "rushing_yards", "receiving": "receiving_yards", "sacks": "defensive_sacks"}


def _home_and_away(event: dict) -> tuple[str, str] | None:
    participants = event.get("participants", [])
    home = next((p for p in participants if p.get("role") == "home"), None)
    away = next((p for p in participants if p.get("role") == "away"), None)
    if home is None or away is None:
        return None
    return home["entity_id"], away["entity_id"]


def _week_key(event: dict) -> tuple:
    """Groups events into what the frontend shows as one "week" --
    postseason games key by their own event_date, not `week`: confirmed
    live that CFBD's postseason week numbering is flat (every bowl/CFP
    round across an entire season comes back as week=1, spanning Dec-Jan),
    so keying postseason by `week` the same way regular season does was
    silently grouping the ENTIRE bracket -- 11+ games spanning a full
    month -- as one "most recently completed week" (confirmed live: a
    real CFP semifinal from 11 days earlier was showing up alongside the
    actual championship game, both generically labeled "CFP" with no way
    to tell them apart). event_date isolates each round correctly instead,
    since a postseason round's own games all share a date, and rounds
    virtually never overlap. Regular season keeps `week`, unaffected."""
    if event.get("season_type") == "postseason":
        return (event.get("season"), event.get("season_type"), event.get("event_date"))
    return (event.get("season"), event.get("season_type"), event.get("week"))


def _previous_week_events(completed: list[dict]) -> list[dict]:
    """Only the most recently completed week's games -- see
    nfl_reads.py's own docstring for the identical reasoning."""
    if not completed:
        return []
    latest = max(completed, key=lambda e: e.get("event_date", ""))
    target = _week_key(latest)
    return [e for e in completed if _week_key(e) == target]


_STALE_SCHEDULED_GRACE_DAYS = 3  # see nfl_reads.py's own docstring for the identical reasoning


def _next_week_events(scheduled: list[dict]) -> list[dict]:
    """Only the soonest upcoming week's games -- see nfl_reads.py's own
    docstring for the identical reasoning, including why events older
    than _STALE_SCHEDULED_GRACE_DAYS are excluded from the min(). The
    grace period only decides which week counts as "soonest" (so an
    already-played Thursday game doesn't hide the rest of its own week's
    Saturday games) -- the returned list is separately filtered to
    today-or-later, since "upcoming" must never include a past-dated
    event, played or not. A "scheduled" event that's actually over a year
    stale gets corrected to "canceled" at write time (see
    aws-lambdas/ncaafb/normalize/handler.py's _cancel_stale_scheduled_
    events) and so drops out of `scheduled` entirely before ever reaching
    here, but this filter still holds for anything more recent than that
    (e.g. a game played yesterday whose status hasn't caught up to
    "completed" yet)."""
    today = datetime.now(timezone.utc).date().isoformat()
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=_STALE_SCHEDULED_GRACE_DAYS)).isoformat()
    plausible = [e for e in scheduled if e.get("event_date", "") >= cutoff]
    if not plausible:
        return []
    earliest = min(plausible, key=lambda e: e.get("event_date", ""))
    target = _week_key(earliest)
    return [e for e in plausible if _week_key(e) == target and e.get("event_date", "") >= today]


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


def _prediction_comparison(rows: list[dict], event: dict) -> dict | None:
    """Compares this event's logged prediction against the actual result
    -- reads the audit trail predict/event_prediction.py's record_prediction
    already wrote, never recomputes one now (see nfl_reads.py's own
    identical docstring for why).

    rows: this event's own predictions-table rows, already fetched by the
    caller (list_events' _entry) -- _leaders_comparison needs the exact
    same query, so it's fetched once and passed to both instead of each
    re-querying it independently."""
    actual = _actual_result(event)
    if actual is None:
        return None

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


def _leaders_comparison(storage, rows: list[dict], sport: str, event: dict) -> dict | None:
    """Player-prop predicted-vs-actual for a completed event. Shape
    mirrors the (predicted-only) `leaders` block predict/event_prediction.py's
    predict_event_leaders returns: `passing` is a single entry or null per
    team (only one passing candidate is ever scored), `rushing`/`receiving`/
    `sacks` are lists.

    rows: same already-fetched predictions-table rows _prediction_
    comparison takes -- see its own docstring for why this isn't queried
    here too."""
    home_away = _home_and_away(event)
    if home_away is None:
        return None
    home_id, away_id = home_away

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

    home: dict[str, list[dict] | dict | None] = {"passing": None, "rushing": [], "receiving": [], "sacks": []}
    away: dict[str, list[dict] | dict | None] = {"passing": None, "rushing": [], "receiving": [], "sacks": []}

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
            # Transferred/left the program since the prediction was
            # recorded, or a lookup failure -- skip rather than guess
            # which side.
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

    for bucket in (home, away):
        for category, limit in _LEADER_CATEGORY_LIMITS.items():
            primary_stat = _CATEGORY_PRIMARY_STAT[category]
            bucket[category].sort(key=lambda entry: entry["predicted"].get(primary_stat, -1), reverse=True)
            bucket[category] = bucket[category][:limit]

    return {"home": home, "away": away}


def _round_label(event: dict) -> str | None:
    """None for a regular-season game or a completed-but-not-yet-flagged
    one; "CFP" for a real 12-team playoff game (is_playoff_game); "Bowl"
    for any other postseason game. Coarser than NFL's own week-number ->
    round-name mapping (POSTSEASON_ROUND_LABELS) -- CFBD's postseason week
    numbering has no equivalent stable per-round meaning across ~40
    unaffiliated bowls plus the CFP, so this is the resolution the data
    actually supports."""
    if event.get("season_type") != "postseason":
        return None
    return "CFP" if is_playoff_game(event) else "Bowl" if is_bowl_game(event) else None


def list_events(storage, predictions_table, sport: str, status: str) -> dict:
    """GET /ncaafb/events?status=scheduled|completed -- scoped to exactly
    one week, not the whole matching history, same convention as
    nfl_reads.list_events (see its own docstring). No exhibition-game
    filter -- unlike NFL's is_real_franchise_matchup, NCAAFB has no
    equivalent contamination to exclude (see
    Source/feature-engineering/ncaafb/build_dataset.py's own docstring).
    Each participant also carries `name`/`abbreviation` off its own team
    entity -- see enrich_participants; this is NCAAFB's only source of
    team display text, unlike NFL's hand-maintained static table."""
    events = storage.get_all_events(sport, status=status)

    if status == "completed":
        events = _previous_week_events(events)
    elif status == "scheduled":
        events = _next_week_events(events)

    def _entry(e: dict) -> dict:
        entry = {
            "event_id": e["event_id"],
            "event_date": e.get("event_date"),
            "kickoff_time": e.get("kickoff_time"),
            "status": e.get("status"),
            "season": e.get("season"),
            "season_type": e.get("season_type"),
            "week": e.get("week"),
            "round": _round_label(e),
            "participants": enrich_participants(storage, sport, e.get("participants")),
            "venue_name": e.get("venue_name"),
        }
        if status == "completed":
            # One query, not two -- _prediction_comparison and
            # _leaders_comparison used to each independently re-query the
            # exact same event_key partition.
            rows = predictions_table.query(Key("event_key").eq(e["event_key"]))
            entry["prediction_comparison"] = _prediction_comparison(rows, e)
            entry["leaders_comparison"] = _leaders_comparison(storage, rows, sport, e)
        return entry

    if not events:
        return {"sport": sport, "events": []}

    # Concurrent, not sequential -- a completed week's own entries each
    # make several DynamoDB round trips (the predictions query above plus
    # a get_entity per matched leader candidate), and events is capped at
    # one week but still turned a real request into a long fully-
    # serialized chain as the predictions table grew (NCAAFB's own
    # completed week can run 60+ FBS games, several times NFL's ~16).
    # Same per-item-independent concurrency shape list_models already
    # uses.
    with ThreadPoolExecutor(max_workers=min(len(events), 16)) as executor:
        entries = list(executor.map(_entry, events))

    return {"sport": sport, "events": entries}


def _load_model_summary(s3, sport: str, model_name: str) -> dict | None:
    """One model's card summary, or None if it's never had a version
    promoted. Same 3-round-trip, run-every-model-concurrently shape as
    nfl_reads._load_model_summary."""
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
        "candidates": card.get("candidates"),
        "candidates_ranked_by": card.get("candidates_ranked_by"),
    }


def list_models(s3, sport: str) -> dict:
    """GET /ncaafb/models -- lists every currently-promoted model
    (win-probability, the three score models, the seven player-prop
    models, national-ranking), with its latest model card summary. A
    model that's never had a version promoted simply doesn't appear in
    this list."""
    prefix = f"{sport}/"
    model_names = sorted({key[len(prefix):].split("/")[0] for key in s3.list_keys(prefix)})

    if not model_names:
        return {"sport": sport, "models": []}

    with ThreadPoolExecutor(max_workers=min(len(model_names), 10)) as executor:
        results = executor.map(lambda name: _load_model_summary(s3, sport, name), model_names)

    return {"sport": sport, "models": [card for card in results if card is not None]}


def get_season_projection(s3, sport: str) -> dict | None:
    """Reads the cached season projection written weekly by the scheduled compute path.
    None if it hasn't run yet."""
    key = season_projection_key(sport)
    if not s3.object_exists(key):
        return None
    return s3.get_json(key)
