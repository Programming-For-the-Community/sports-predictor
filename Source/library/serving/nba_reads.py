"""
Read-only NBA serving logic -- GET /nba/events, GET /nba/models -- shared
between the heavy inference Lambda (Source/aws-lambdas/nba/predict) and
the light read-only Lambda (Source/aws-lambdas/nba/predict-read). Same
split, and same reasoning, as library.serving.ncaafb_reads (see its own
docstring) -- NOT a port of it, duplicated deliberately so this Lambda
never has to import that NCAAFB-named module.

get_season_projection (Sub-phase 3A step 8) just reads the standings +
NBA Cup + player-prop leaderboard projection written weekly by the
scheduled compute path (aws-lambdas/nba/predict/season_projection.py's
run_scheduled) -- never computed live here, same read-only-cache role
nfl_reads.py's/ncaafb_reads.py's own get_season_projection play. No round
label -- NBA has no postseason-round concept the way NCAAFB's national
ranking does.

No round/week grouping -- NBA has no CFBD-style week numbering (ESPN's
schedule is date-based, ~10-15 games/night most nights of the season), so
list_events groups by calendar date instead of week, unlike nfl_reads.py/
ncaafb_reads.py's own _previous_week_events/_next_week_events.

Callers own their own storage/s3/predictions_table objects and Lambda-
lifecycle concerns, same boundary nfl_reads.py/ncaafb_reads.py draw.
"""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from boto3.dynamodb.conditions import Key

from library.serving.common import enrich_participants
from library.storage.model_artifacts import current_version_key, model_artifact_key
from library.storage.season_projections import season_projection_key

_PLAYER_PROP_MODEL_KEY_RE = re.compile(r"^MODEL#player-prop-([a-z-]+)#v\d+#PLAYER#(.+)$")

WIN_PROBABILITY_MODEL = "win-probability"
SCORE_MODELS = {"margin": "score-margin", "home_score": "home-score", "away_score": "away-score"}

# Mirrors predict/event_prediction.py's own LEADER_CATEGORY_STATS, inverted
# (stat -> category instead of category -> stats) -- duplicated rather than
# imported, same reasoning ncaafb_reads.py's own _STAT_CATEGORY gives.
# Basketball's 3 categories (no football-style passing/rushing/receiving/
# sacks split -- no position concept, see library.features.nba's own
# docstring) each key off exactly one primary stat.
_STAT_CATEGORY = {"points": "scoring", "rebounds": "rebounding", "assists": "assists"}

# Mirrors predict/event_prediction.py's own LEADER_CATEGORY_STATS primary
# stat per category -- duplicated for the same reason _STAT_CATEGORY above
# is. 2 leaders per category -- NBA box scores are far less crowded than
# football depth charts (10-12 rotation players, not ~50), and every
# category is a list here (no NCAAFB-style singular "passing" category --
# no leader is inherently singular in basketball the way a starting QB is).
_LEADER_CATEGORY_LIMITS = {"scoring": 2, "rebounding": 2, "assists": 2}
_CATEGORY_PRIMARY_STAT = {"scoring": "points", "rebounding": "rebounds", "assists": "assists"}


def _home_and_away(event: dict) -> tuple[str, str] | None:
    participants = event.get("participants", [])
    home = next((p for p in participants if p.get("role") == "home"), None)
    away = next((p for p in participants if p.get("role") == "away"), None)
    if home is None or away is None:
        return None
    return home["entity_id"], away["entity_id"]


def _previous_day_events(completed: list[dict]) -> list[dict]:
    """Only the most recently completed date's games -- the date-based
    analog of nfl_reads.py's/ncaafb_reads.py's own _previous_week_events."""
    if not completed:
        return []
    latest_date = max(e.get("event_date", "") for e in completed)
    return [e for e in completed if e.get("event_date") == latest_date]


def _next_day_events(scheduled: list[dict]) -> list[dict]:
    """Only the soonest upcoming date's games. No grace-period/cutoff step
    the way nfl_reads.py's/ncaafb_reads.py's own _next_week_events need --
    that exists there to stop one already-played game from hiding the rest
    of its own week's later games, which only matters because a "week" of
    theirs spans several dates. Grouping by single calendar date here makes
    that scenario moot: filtering straight to today-or-later before picking
    the earliest date is both simpler and correct."""
    today = datetime.now(timezone.utc).date().isoformat()
    plausible = [e for e in scheduled if e.get("event_date", "") >= today]
    if not plausible:
        return []
    earliest_date = min(e.get("event_date", "") for e in plausible)
    return [e for e in plausible if e.get("event_date") == earliest_date]


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
    """Compares this event's logged prediction against the actual result --
    reads the audit trail predict/event_prediction.py's record_prediction
    already wrote, never recomputes one now (see nfl_reads.py's own
    identical docstring for why)."""
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
    """Player-prop predicted-vs-actual for a completed event. Shape mirrors
    the (predicted-only) `leaders` block predict/event_prediction.py's
    predict_event_leaders returns: scoring/rebounding/assists are each
    always a list (no singular category, unlike NCAAFB's passing)."""
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

    home: dict[str, list[dict]] = {"scoring": [], "rebounding": [], "assists": []}
    away: dict[str, list[dict]] = {"scoring": [], "rebounding": [], "assists": []}

    for entity_id, predicted_stats in predicted_by_entity.items():
        category = next((_STAT_CATEGORY[stat] for stat in predicted_stats if stat in _STAT_CATEGORY), None)
        if category is None:
            continue
        entity = storage.get_entity(sport, entity_id, "player")
        team_id = (entity.get("metadata") or {}).get("team_id") if entity else None
        if team_id == home_id:
            bucket = home
        elif team_id == away_id:
            bucket = away
        else:
            # Traded/waived since the prediction was recorded, or a lookup
            # failure -- skip rather than guess which side.
            continue

        actual_stats = actual_by_entity.get(entity_id, {})
        entry = {
            "entity_id": entity_id,
            "predicted": predicted_stats,
            "actual": {stat: actual_stats[stat] for stat in predicted_stats if stat in actual_stats},
        }
        if entity and entity.get("name"):
            entry["name"] = entity["name"]
        bucket[category].append(entry)

    for bucket in (home, away):
        for category, limit in _LEADER_CATEGORY_LIMITS.items():
            primary_stat = _CATEGORY_PRIMARY_STAT[category]
            bucket[category].sort(key=lambda entry: entry["predicted"].get(primary_stat, -1), reverse=True)
            bucket[category] = bucket[category][:limit]

    return {"home": home, "away": away}


def list_events(storage, predictions_table, sport: str, status: str) -> dict:
    """GET /nba/events?status=scheduled|completed -- scoped to exactly one
    calendar date, not the whole matching history (see this module's own
    docstring for why NBA groups by date instead of week). Each participant
    also carries `name`/`abbreviation` off its own team entity -- see
    enrich_participants."""
    events = storage.get_all_events(sport, status=status)

    if status == "completed":
        events = _previous_day_events(events)
    elif status == "scheduled":
        events = _next_day_events(events)

    def _entry(e: dict) -> dict:
        entry = {
            "event_id": e["event_id"],
            "event_date": e.get("event_date"),
            "kickoff_time": e.get("kickoff_time"),
            "status": e.get("status"),
            "season": e.get("season"),
            "participants": enrich_participants(storage, sport, e.get("participants")),
            "venue_name": e.get("venue_name"),
        }
        if status == "completed":
            # One query, not two -- _prediction_comparison and
            # _leaders_comparison need the exact same predictions-table
            # partition, same "fetch once, pass to both" shape
            # ncaafb_reads.py's own _entry uses.
            rows = predictions_table.query(Key("event_key").eq(e["event_key"]))
            entry["prediction_comparison"] = _prediction_comparison(rows, e)
            entry["leaders_comparison"] = _leaders_comparison(storage, rows, sport, e)
        return entry

    if not events:
        return {"sport": sport, "events": []}

    # Concurrent, not sequential -- a completed date's own entries each make
    # several DynamoDB round trips, same reasoning ncaafb_reads.list_events
    # gives (though NBA's own nightly slate, ~10-15 games, is far smaller
    # than NCAAFB's 60+-game week).
    with ThreadPoolExecutor(max_workers=min(len(events), 16)) as executor:
        entries = list(executor.map(_entry, events))

    return {"sport": sport, "events": entries}


def _load_model_summary(s3, sport: str, model_name: str) -> dict | None:
    """One model's card summary, or None if it's never had a version
    promoted. Same 3-round-trip, run-every-model-concurrently shape as
    nfl_reads._load_model_summary/ncaafb_reads._load_model_summary."""
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
    """GET /nba/models -- lists every currently-promoted model
    (win-probability, the three score models, the six player-prop models),
    with its latest model card summary. A model that's never had a version
    promoted simply doesn't appear in this list."""
    prefix = f"{sport}/"
    model_names = sorted({key[len(prefix):].split("/")[0] for key in s3.list_keys(prefix)})

    if not model_names:
        return {"sport": sport, "models": []}

    with ThreadPoolExecutor(max_workers=min(len(model_names), 10)) as executor:
        results = executor.map(lambda name: _load_model_summary(s3, sport, name), model_names)

    return {"sport": sport, "models": [card for card in results if card is not None]}


def get_season_projection(s3, sport: str) -> dict | None:
    """GET /nba/season -- reads the standings + NBA Cup + leaderboard
    projection written weekly by the scheduled compute path (predict/
    handler.py's ScheduledSeasonProjection branch), never computed live
    here. None if the schedule hasn't fired yet (e.g. right after a fresh
    deploy) -- the caller is expected to surface that as "not yet
    available" rather than treat it like a real 500. Identical to
    nfl_reads.py's/ncaafb_reads.py's own get_season_projection -- the S3
    read-through shape has no sport-specific logic in it at all."""
    key = season_projection_key(sport)
    if not s3.object_exists(key):
        return None
    return s3.get_json(key)
