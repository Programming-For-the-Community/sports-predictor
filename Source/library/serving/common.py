"""
Sport-agnostic serving helpers shared across sports' *_reads.py modules.
"""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from boto3.dynamodb.conditions import Key

from library.parsing import us_eastern_date
from library.storage.model_artifacts import current_version_key, model_artifact_key
from library.storage.season_projections import season_projection_key

# Every head-to-head sport's own predict/event_prediction_common.py writes
# its predictions-table audit trail under these same model-key prefixes
# (WIN_PROBABILITY_MODEL/SCORE_MODELS) and player-prop rows matching
# _PLAYER_PROP_MODEL_KEY_RE -- confirmed identical across nfl/nba/ncaafb/
# ncaambb before sharing here, since a *_reads.py's own _prediction_
# comparison/_leaders_comparison below only make sense if they're reading
# back exactly what was written.
WIN_PROBABILITY_MODEL = "win-probability"
SCORE_MODELS = {"margin": "score-margin", "home_score": "home-score", "away_score": "away-score"}
_PLAYER_PROP_MODEL_KEY_RE = re.compile(r"^MODEL#player-prop-([a-z-]+)#v\d+#PLAYER#(.+)$")

# Every head-to-head sport's own list_events narrows a get_all_events call
# down to just the single most recent (completed) or soonest (scheduled)
# day/week's games -- this bounds the query itself to that many of the
# most-recent/soonest rows instead of a full-history read that grows with
# the whole season/backfill. Comfortably above any sport's own documented
# single-date/week peak (NCAA MBB's ~150-game Saturday, NCAAFB's full FBS
# weekly slate), so the true most/least recent bucket's full slate is
# always included regardless of how long the gap to it is (e.g. the
# off-season).
RECENT_EVENTS_LIMIT = 400


def enrich_participants(
    storage, sport: str, participants: list[dict] | None, entity_type: str = "team",
    entity_cache: dict[tuple[str, str], dict] | None = None,
) -> list[dict] | None:
    """Attaches each participant's own entity name/abbreviation/conference/
    color. One get_entity per participant, UNLESS entity_cache is given
    (see prefetch_entities) -- then a cache hit costs nothing, and a miss
    (a ref the caller's own prefetch didn't include) still falls back to
    get_entity rather than silently rendering blank. Callers with many
    participants across many events (a field-event sport's own list_events,
    e.g. up to ~150 golfers per PGA tournament) should prefetch first --
    the plain per-participant GetItem path here is fine for the common
    single-event case (2-6 participants), same as before entity_cache
    existed.

    entity_type defaults to "team" for head-to-head sports' participants;
    field-event sports (PGA, F1) pass entity_type="player" instead, since
    their participants are individual athlete entities with no team to look
    up -- abbreviation/conference/color just degrade to None for those the
    same way a missing entity already does, rather than needing a separate
    player-shaped enrichment function."""
    if not participants:
        return participants

    enriched = []
    for participant in participants:
        entity_id = participant["entity_id"]
        if entity_cache is not None:
            entity = entity_cache.get((entity_id, entity_type)) or storage.get_entity(sport, entity_id, entity_type)
        else:
            entity = storage.get_entity(sport, entity_id, entity_type)
        metadata = (entity or {}).get("metadata") or {}
        enriched.append({
            **participant,
            "name": (entity or {}).get("name"),
            "abbreviation": metadata.get("abbreviation"),
            "conference": metadata.get("conference"),
            "color": metadata.get("color"),
        })
    return enriched


def prefetch_entities(storage, sport: str, refs: list[tuple[str, str]]) -> dict[tuple[str, str], dict]:
    """Thin pass-through to FeatureStorage.get_entities (BatchGetItem,
    deduplicated) -- named/re-exported here so a *_reads.py caller
    building an entity_cache for enrich_participants doesn't need to know
    FeatureStorage's own method name, matching how enrich_participants
    already hides get_entity itself from callers."""
    return storage.get_entities(sport, refs)


def most_recent_event(events: list[dict]) -> list[dict]:
    """The single most recently-dated event, wrapped in a list (empty if
    `events` is empty). For a field-event sport whose grouping unit is
    already "one tournament"/"one race weekend" (one event_key) -- unlike
    a head-to-head sport's own per-week/per-day bucketing (nfl_reads.py's
    _previous_week_events and siblings), which groups MULTIPLE games
    sharing a week/day, there's no smaller natural bucket here to filter
    down to. Used by pga_reads.py/f1_reads.py's own list_events to bound a
    status=completed response to the same "just the most recent bucket,
    not full history" shape every other sport's list_events already has."""
    if not events:
        return []
    return [max(events, key=lambda e: e.get("event_date", ""))]


def enrich_team_standings(storage, sport: str, standings: list[dict]) -> list[dict]:
    """Same purpose as enrich_participants, for standings rows (keyed by
    team_id, no role/result)."""
    enriched = []
    for row in standings:
        entity = storage.get_entity(sport, row["team_id"], "team")
        metadata = (entity or {}).get("metadata") or {}
        enriched.append({
            **row,
            "name": (entity or {}).get("name"),
            "abbreviation": metadata.get("abbreviation"),
            "color": metadata.get("color"),
        })
    return enriched


def _add_matchup_team_ids(matchup: dict, team_ids: set[str]) -> None:
    for key in ("team_a", "team_b"):
        if matchup.get(key):
            team_ids.add(matchup[key])


def _add_matchups_team_ids(matchups: list[dict], team_ids: set[str]) -> None:
    for matchup in matchups:
        _add_matchup_team_ids(matchup, team_ids)


def _add_rounds_team_ids(rounds: list[dict], team_ids: set[str]) -> None:
    for round_ in rounds:
        _add_matchups_team_ids(round_["matchups"], team_ids)


def _bracket_team_ids(bracket: dict) -> set[str]:
    """Every distinct team id across a bracket payload's matchup rows,
    whichever of its shapes (conference rounds, a flat round list, NCAA
    MBB's own region/First-Four/Final-Four shape, a single championship-
    style matchup) it actually has."""
    team_ids: set[str] = set()
    for rounds in bracket.get("conferences", {}).values():
        _add_rounds_team_ids(rounds, team_ids)
    if bracket.get("rounds"):
        _add_rounds_team_ids(bracket["rounds"], team_ids)
    # NCAA MBB's March Madness bracket only -- 4 separate region brackets
    # (each its own round list) plus the First Four/Final Four's own flat
    # matchup lists, kept apart from `rounds` so the frontend can draw the
    # traditional region layout instead of one flat list.
    for region in bracket.get("regions", {}).values():
        _add_rounds_team_ids(region["rounds"], team_ids)
    for key in ("first_four", "final_four"):
        if bracket.get(key):
            _add_matchups_team_ids(bracket[key], team_ids)
    for key in ("super_bowl", "finals", "championship"):
        if bracket.get(key):
            _add_matchup_team_ids(bracket[key], team_ids)
    return team_ids


def enrich_bracket_team_names(storage, sport: str, bracket: dict) -> dict:
    """Attaches a {team_id: {"name", "abbreviation", "color"}} lookup
    (`team_names`) to a bracket payload -- collects every distinct team id
    across all matchup rows and looks each up exactly once."""
    team_names = {}
    for team_id in _bracket_team_ids(bracket):
        entity = storage.get_entity(sport, team_id, "team")
        metadata = (entity or {}).get("metadata") or {}
        team_names[team_id] = {
            "name": (entity or {}).get("name"),
            "abbreviation": metadata.get("abbreviation"),
            "color": metadata.get("color"),
        }

    return {**bracket, "team_names": team_names}


def _load_model_summary(s3, sport: str, model_name: str) -> dict | None:
    """One model's card summary, or None if it's never had a version
    promoted. Added here 2026-08-27 for library.serving.pga_reads --
    every head-to-head sport's own *_reads.py (nba/ncaafb/ncaambb/nfl)
    still carries its own pre-existing copy of this function; left alone
    rather than rewired to import from here, to avoid touching four
    already-working, already-deployed serving Lambdas for a refactor this
    task doesn't need. A future cleanup could point them here too."""
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
    """GET /{sport}/models -- lists every currently-promoted model, with
    its latest model card summary. A model that's never had a version
    promoted simply doesn't appear in this list. Fully generic over S3
    key prefixes -- works unchanged regardless of a sport's own model set
    (win-probability + score models for a head-to-head sport, top-10/
    top-5/score/cutline/round/match/cup for PGA's field-event shape)."""
    prefix = f"{sport}/"
    model_names = sorted({key[len(prefix):].split("/")[0] for key in s3.list_keys(prefix)})

    if not model_names:
        return {"sport": sport, "models": []}

    with ThreadPoolExecutor(max_workers=min(len(model_names), 10)) as executor:
        results = executor.map(lambda name: _load_model_summary(s3, sport, name), model_names)

    return {"sport": sport, "models": [card for card in results if card is not None]}


def get_season_projection(s3, sport: str) -> dict | None:
    """GET /{sport}/season -- reads the standings/probability projection
    written weekly by that sport's own scheduled compute path, never
    computed live here. None if the schedule hasn't fired yet -- the
    caller is expected to surface that as "not yet available" rather than
    treat it like a real 500. Confirmed identical across all 6 sports
    (only the docstring on what exactly the projection contains --
    standings, NBA Cup, brackets, FedEx Cup, championship -- ever
    differed)."""
    key = season_projection_key(sport)
    if not s3.object_exists(key):
        return None
    return s3.get_json(key)


def _home_and_away(event: dict) -> tuple[str, str] | None:
    participants = event.get("participants", [])
    home = next((p for p in participants if p.get("role") == "home"), None)
    away = next((p for p in participants if p.get("role") == "away"), None)
    if home is None or away is None:
        return None
    return home["entity_id"], away["entity_id"]


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
    reads the audit trail predict/event_prediction_common.py's
    record_prediction already wrote, never recomputes one now. Recomputing
    after the fact would build live features from rolling averages/Elo
    that may already include this game's own now-normalized stats, leaking
    the outcome into its own "prediction". Returns None if no prediction
    was ever logged for this event, or it has no final score yet.

    rows: this event's own predictions-table rows, already fetched by the
    caller and shared with a leaders-comparison helper rather than
    re-queried."""
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


# Basketball-shaped (nba/ncaambb only -- both day-grouped schedules, both
# scoring/rebounding/assists as three uniformly-list-valued leader
# categories, confirmed byte-identical between the two before sharing
# here). NFL/NCAAFB's own week-grouped, singular-"passing"-category shape
# is different enough (see each sport's own *_reads.py) that it isn't
# folded in here.
_BASKETBALL_STAT_CATEGORY = {"points": "scoring", "rebounds": "rebounding", "assists": "assists"}
_BASKETBALL_LEADER_CATEGORY_LIMITS = {"scoring": 5, "rebounding": 5, "assists": 5}
_BASKETBALL_CATEGORY_PRIMARY_STAT = {"scoring": "points", "rebounding": "rebounds", "assists": "assists"}


def _previous_day_events(completed: list[dict]) -> list[dict]:
    """Only the most recently completed date's games."""
    if not completed:
        return []
    latest_date = max(e.get("event_date", "") for e in completed)
    return [e for e in completed if e.get("event_date") == latest_date]


def _next_day_events(scheduled: list[dict]) -> list[dict]:
    """Only the soonest upcoming date's games. No grace-period/cutoff step
    needed: grouping by single calendar date means filtering straight to
    today-or-later before picking the earliest date is both simpler and
    correct."""
    # event_date is a calendar day in ESPN's own U.S.-Eastern bucketing
    # (see library/parsing.py's us_eastern_date), not a UTC date --
    # comparing it against a raw UTC "today" drops today's games from this
    # list the moment the server clock crosses UTC midnight, which for a
    # 6pm+ Eastern tip-off is while it's still being played. Deriving
    # "today" the same Eastern way keeps both sides on the same calendar.
    today = us_eastern_date(datetime.now(timezone.utc))
    plausible = [e for e in scheduled if e.get("event_date", "") >= today]
    if not plausible:
        return []
    earliest_date = min(e.get("event_date", "") for e in plausible)
    return [e for e in plausible if e.get("event_date") == earliest_date]


def _basketball_leaders_comparison(storage, rows: list[dict], sport: str, event: dict) -> dict | None:
    """Player-prop predicted-vs-actual for a completed event. Shape mirrors
    the (predicted-only) `leaders` block predict_event_leaders returns:
    scoring/rebounding/assists are each always a list (no singular
    category, unlike NCAAFB's passing).

    Grouped by (entity_id, category), not entity_id alone -- a star player
    is routinely a genuine candidate in more than one category (scoring
    AND rebounding AND assists), scored separately per category by
    predict_event_leaders (a distinct MODEL#player-prop-<stat># row per
    stat). Keying only on entity_id previously merged every category's
    stats for that player into one dict, then filed the WHOLE merged dict
    under whichever single category won an arbitrary dict-iteration-order
    tiebreak -- a real complaint 2026-09-xx (NCAAFB, same shared pattern):
    a player's row in one category's own list was showing an unrelated
    category's stats appended after it. Each (entity_id, category) pair
    now gets its own entry, correctly scoped to just that category's own
    stats, and can appear in more than one category's own list, same as
    the predicted-only leaders panel already allows."""
    home_away = _home_and_away(event)
    if home_away is None:
        return None
    home_id, away_id = home_away

    predicted_by_entity_category = _predicted_stats_by_entity_category(rows, _BASKETBALL_STAT_CATEGORY)
    if not predicted_by_entity_category:
        return None

    actual_by_entity = {
        row["entity_id"]: row.get("stat_line", {})
        for row in storage.get_player_game_stats_for_event(event["event_key"])
    }

    home: dict[str, list[dict]] = {"scoring": [], "rebounding": [], "assists": []}
    away: dict[str, list[dict]] = {"scoring": [], "rebounding": [], "assists": []}
    _bucket_leader_entries(
        storage, sport, predicted_by_entity_category, home_id, away_id, actual_by_entity, home, away, set(),
    )
    _sort_and_limit_leader_lists(home, away, _BASKETBALL_LEADER_CATEGORY_LIMITS, _BASKETBALL_CATEGORY_PRIMARY_STAT)

    return {"home": home, "away": away}


def _predicted_stats_by_entity_category(
    rows: list[dict], stat_category: dict[str, str],
) -> dict[tuple[str, str], dict[str, float]]:
    """{(entity_id, category): {stat: predicted_value}} from this event's
    own predictions-table rows, keyed by (entity_id, category) rather than
    entity_id alone -- see _basketball_leaders_comparison/
    _football_leaders_comparison's own docstrings for why."""
    predicted_by_entity_category: dict[tuple[str, str], dict[str, float]] = {}
    for row in rows:
        match = _PLAYER_PROP_MODEL_KEY_RE.match(row["model_key"])
        if match is None:
            continue
        stat = match.group(1).replace("-", "_")
        category = stat_category.get(stat)
        if category is None:
            continue
        entity_id = match.group(2)
        predicted_by_entity_category.setdefault((entity_id, category), {})[stat] = row["predicted_value"]["value"]
    return predicted_by_entity_category


def _leader_entry(entity: dict | None, entity_id: str, predicted_stats: dict[str, float], actual_by_entity: dict[str, dict]) -> dict:
    actual_stats = actual_by_entity.get(entity_id, {})
    entry = {
        "entity_id": entity_id,
        "predicted": predicted_stats,
        "actual": {stat: actual_stats[stat] for stat in predicted_stats if stat in actual_stats},
    }
    if entity and entity.get("name"):
        entry["name"] = entity["name"]
    return entry


def _bucket_leader_entries(
    storage, sport: str, predicted_by_entity_category: dict[tuple[str, str], dict[str, float]],
    home_id: str, away_id: str, actual_by_entity: dict[str, dict], home: dict, away: dict,
    singular_categories: set[str],
) -> None:
    """Buckets each (entity_id, category) candidate's predicted-vs-actual
    entry into home/away by the entity's own current team_id -- mutates
    home/away in place. singular_categories names any category stored as a
    single entry-or-None (football's own "passing") rather than a list."""
    entity_cache: dict[str, dict | None] = {}
    for (entity_id, category), predicted_stats in predicted_by_entity_category.items():
        if entity_id not in entity_cache:
            entity_cache[entity_id] = storage.get_entity(sport, entity_id, "player")
        entity = entity_cache[entity_id]
        team_id = (entity.get("metadata") or {}).get("team_id") if entity else None
        if team_id == home_id:
            bucket = home
        elif team_id == away_id:
            bucket = away
        else:
            # Traded/waived/transferred since the prediction was recorded,
            # or a lookup failure -- skip rather than guess which side.
            continue

        entry = _leader_entry(entity, entity_id, predicted_stats, actual_by_entity)
        if category in singular_categories:
            bucket[category] = entry
        else:
            bucket[category].append(entry)


def _sort_and_limit_leader_lists(home: dict, away: dict, limits: dict[str, int], primary_stat: dict[str, str]) -> None:
    """Re-sorts and slices each list-valued category bucket to its own
    limit, by that category's own primary stat -- mutates home/away in
    place."""
    for bucket in (home, away):
        for category, limit in limits.items():
            stat = primary_stat[category]
            bucket[category].sort(key=lambda entry, stat=stat: entry["predicted"].get(stat, -1), reverse=True)
            bucket[category] = bucket[category][:limit]


def list_events_grouped_by_day(storage, predictions_table, sport: str, status: str) -> dict:
    """GET /{sport}/events?status=scheduled|completed for a day-grouped
    basketball-shaped sport (nba/ncaambb) -- scoped to exactly one calendar
    date, not the whole matching history. Each participant also carries
    `name`/`abbreviation` off its own team entity -- see enrich_participants.
    Also carries `venue_name`/`venue_city`/`venue_state` straight off the
    stored event, `null` on any of the three the venue lacked.

    Bounded to RECENT_EVENTS_LIMIT rows on the query itself, most-recent-
    or soonest-first to match whichever bucket status narrows down to
    below -- an unbounded get_all_events call here would paginate through
    the sport's entire completed/scheduled history before ever discarding
    everything but one date (see pga_reads.py's own list_events
    docstring)."""
    if status == "completed":
        events = storage.get_all_events(sport, status=status, limit=RECENT_EVENTS_LIMIT)
        events = _previous_day_events(events)
    elif status == "scheduled":
        events = storage.get_all_events(sport, status=status, scan_index_forward=True, limit=RECENT_EVENTS_LIMIT)
        events = _next_day_events(events)
    else:
        events = storage.get_all_events(sport, status=status)

    def _entry(e: dict) -> dict:
        entry = {
            "event_id": e["event_id"],
            "event_date": e.get("event_date"),
            "kickoff_time": e.get("kickoff_time"),
            "status": e.get("status"),
            "season": e.get("season"),
            "participants": enrich_participants(storage, sport, e.get("participants")),
            "venue_name": e.get("venue_name"),
            "venue_city": e.get("venue_city"),
            "venue_state": e.get("venue_state"),
        }
        if status == "completed":
            # One query shared by _prediction_comparison and
            # _basketball_leaders_comparison rather than each querying
            # independently.
            rows = predictions_table.query(Key("event_key").eq(e["event_key"]))
            entry["prediction_comparison"] = _prediction_comparison(rows, e)
            entry["leaders_comparison"] = _basketball_leaders_comparison(storage, rows, sport, e)
        return entry

    if not events:
        return {"sport": sport, "events": []}

    # Concurrent, not sequential -- each entry makes several DynamoDB round
    # trips, independent per event.
    with ThreadPoolExecutor(max_workers=min(len(events), 16)) as executor:
        entries = list(executor.map(_entry, events))

    return {"sport": sport, "events": entries}


# Football-shaped (nfl/ncaafb only -- confirmed identical leader-category
# shape and _leaders_comparison body between the two before sharing here;
# dict key order below differs from either sport's own original literal,
# which is fine, Python dict equality and JSON consumers alike ignore key
# order). Both sports' own list_events stays separate (week-grouping,
# round-label, and -- nfl only -- exhibition-game filtering genuinely
# differ; see each sport's own *_reads.py).
_FOOTBALL_STAT_CATEGORY = {
    "passing_yards": "passing", "passing_touchdowns": "passing",
    "receiving_yards": "receiving", "receiving_touchdowns": "receiving",
    "rushing_yards": "rushing", "rushing_touchdowns": "rushing",
    "defensive_sacks": "sacks",
}
_FOOTBALL_LEADER_CATEGORY_LIMITS = {"receiving": 3, "rushing": 2, "sacks": 3}
_FOOTBALL_CATEGORY_PRIMARY_STAT = {"receiving": "receiving_yards", "rushing": "rushing_yards", "sacks": "defensive_sacks"}


def _football_leaders_comparison(storage, rows: list[dict], sport: str, event: dict) -> dict | None:
    """Player-prop predicted-vs-actual for a completed event. Shape mirrors
    the (predicted-only) `leaders` block predict_event_leaders returns:
    `passing` is a single entry or null per team (only one passing
    candidate is ever scored), `receiving`/`rushing`/`sacks` are lists.

    Grouped by (entity_id, category), not entity_id alone -- a versatile
    player (e.g. a receiving back) can be a genuine candidate in more than
    one category, scored separately per category by predict_event_leaders
    (a distinct MODEL#player-prop-<stat># row per stat). Keying only on
    entity_id previously merged every category's stats for that player
    into one dict, then filed the WHOLE merged dict under whichever single
    category won an arbitrary dict-iteration-order tiebreak -- each
    (entity_id, category) pair now gets its own entry, correctly scoped to
    just that category's own stats, and can appear in more than one
    category's own list, same as the predicted-only leaders panel already
    allows."""
    home_away = _home_and_away(event)
    if home_away is None:
        return None
    home_id, away_id = home_away

    predicted_by_entity_category = _predicted_stats_by_entity_category(rows, _FOOTBALL_STAT_CATEGORY)
    if not predicted_by_entity_category:
        return None

    actual_by_entity = {
        row["entity_id"]: row.get("stat_line", {})
        for row in storage.get_player_game_stats_for_event(event["event_key"])
    }

    home: dict[str, list[dict] | dict | None] = {"passing": None, "receiving": [], "rushing": [], "sacks": []}
    away: dict[str, list[dict] | dict | None] = {"passing": None, "receiving": [], "rushing": [], "sacks": []}
    _bucket_leader_entries(
        storage, sport, predicted_by_entity_category, home_id, away_id, actual_by_entity, home, away, {"passing"},
    )
    _sort_and_limit_leader_lists(home, away, _FOOTBALL_LEADER_CATEGORY_LIMITS, _FOOTBALL_CATEGORY_PRIMARY_STAT)

    return {"home": home, "away": away}
