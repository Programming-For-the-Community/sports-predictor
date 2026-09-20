"""
Shared ESPN backfill primitives (nba/ncaambb -- confirmed byte-identical
before sharing here, aside from the sport string used in S3 key prefixes).
nfl's own backfill.py differs structurally (week/season-type loop, no
process_date) and has no test coverage to verify a merge against, so it
stays fully separate; ncaafb/f1/pga are structurally distinct (no team
seeding, round-based fetches) and are also untouched.

Each sport's own backfill.py keeps `seed_teams(client, storage)` and
`process_game(client, storage, season, event_id)` under their original,
tested signatures, delegating to `seed_teams`/`process_game` below with
their own `normalize` module and sport string bound in. Safe to share
because both only call through `normalize_module` as a MODULE reference,
not individually-imported bare function names -- `patch.object(backfill.
normalize, "team_to_entity", ...)` mutates the one module object the
shared function's own `normalize_module.team_to_entity(...)` call reads
too.

`process_date`/`process_season`/`process_batch`/`main` stay fully
per-sport -- ncaambb adds AP-poll ranking seeding and per-event
ThreadPoolExecutor concurrency that nba doesn't have, and nba has a
preseason-skip check ncaambb doesn't need (see each sport's own module
docstring); not the same shape despite `seed_teams`/`process_game` being
identical.
"""
from library.storage.pipeline_storage import PipelineStorage


def seed_teams(client, storage: PipelineStorage, normalize_module, sport: str, logger) -> None:
    """ESPN's /teams isn't season-scoped, so one global call seeds every team."""
    logger.info("Seeding team entities")
    teams_response = client.get_teams()
    storage.put_raw_json(f"{sport}/teams.json", teams_response)
    league = teams_response["sports"][0]["leagues"][0]
    for team_entry in league["teams"]:
        storage.upsert_entity(normalize_module.team_to_entity(team_entry["team"]))
    logger.info("Seeded %d teams", len(league["teams"]))


def process_game(client, storage: PipelineStorage, normalize_module, sport: str, season: int, event_id: str, logger) -> None:
    raw_key = f"{sport}/boxscore/{season}/{event_id}.json"
    if storage.raw_object_exists(raw_key):
        logger.debug("Box score already loaded, skipping event %s", event_id)
        return
    summary = client.get_summary(event_id)
    storage.put_raw_json(raw_key, summary)
    stats_items, player_entities = normalize_module.boxscore_to_player_game_stats(summary)
    for entity in player_entities:
        storage.upsert_player_entity(entity)
    storage.write_player_game_stats(stats_items)
    storage.write_team_game_stats(normalize_module.boxscore_to_team_game_stats(summary))
