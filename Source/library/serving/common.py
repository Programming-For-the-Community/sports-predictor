"""
Sport-agnostic serving helpers shared across sports' *_reads.py modules.
"""
from concurrent.futures import ThreadPoolExecutor

from library.storage.model_artifacts import current_version_key, model_artifact_key

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


def enrich_bracket_team_names(storage, sport: str, bracket: dict) -> dict:
    """Attaches a {team_id: {"name", "abbreviation", "color"}} lookup
    (`team_names`) to a bracket payload -- collects every distinct team id
    across all matchup rows and looks each up exactly once."""
    team_ids: set[str] = set()

    def _collect_matchup(matchup: dict) -> None:
        for key in ("team_a", "team_b"):
            if matchup.get(key):
                team_ids.add(matchup[key])

    def _collect_matchups(matchups: list[dict]) -> None:
        for matchup in matchups:
            _collect_matchup(matchup)

    def _collect_rounds(rounds: list[dict]) -> None:
        for round_ in rounds:
            _collect_matchups(round_["matchups"])

    for rounds in bracket.get("conferences", {}).values():
        _collect_rounds(rounds)
    if bracket.get("rounds"):
        _collect_rounds(bracket["rounds"])
    # NCAA MBB's March Madness bracket only -- 4 separate region brackets
    # (each its own round list) plus the First Four/Final Four's own flat
    # matchup lists, kept apart from `rounds` so the frontend can draw the
    # traditional region layout instead of one flat list.
    for region in bracket.get("regions", {}).values():
        _collect_rounds(region["rounds"])
    for key in ("first_four", "final_four"):
        if bracket.get(key):
            _collect_matchups(bracket[key])
    for key in ("super_bowl", "finals", "championship"):
        if bracket.get(key):
            _collect_matchup(bracket[key])

    team_names = {}
    for team_id in team_ids:
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
