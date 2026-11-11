"""
Saves CFBD's feature supplements to the raw bucket, each entry tagged with
our team_id (CFBD names teams by school): a week's advanced game stats once
any of its games is final (re-fetched each run, like the box scores), and
a season's talent composite and returning production once each. Read back
by library.storage.cfbd_supplements.
"""
import logging

from library.storage import cfbd_supplements
from library.storage.ncaafb_team_cache import teams_by_school

logger = logging.getLogger(__name__)


def tag_team_ids(entries: list[dict], school_to_id: dict[str, str]) -> list[dict]:
    """Entries whose `team` school is known, each with its team_id."""
    tagged = []
    for entry in entries or []:
        team_id = school_to_id.get(entry.get("team")) if isinstance(entry, dict) else None
        if team_id is not None:
            tagged.append({**entry, "team_id": team_id})
    return tagged


def _save_once(key: str, fetch, school_to_id: dict[str, str], *, object_exists, put_json) -> bool:
    if object_exists(key):
        return False
    entries = tag_team_ids(fetch(), school_to_id)
    if not entries:
        logger.info("Nothing listed yet for %s -- will try again next run", key)
        return False
    put_json(key, entries)
    return True


def save_supplements(
    client, season: int, season_type: str, week: int, season_teams: list[dict], any_completed: bool,
    *, object_exists, put_json,
) -> dict[str, bool]:
    """Which supplements this run saved. Each is attempted on its own: a
    failure is logged and the rest still run."""
    school_to_id = {school: str(team["id"]) for school, team in teams_by_school(season_teams).items() if team.get("id") is not None}
    saved = {"advanced": False, "talent": False, "returning": False}
    if not school_to_id:
        logger.warning("No season teams to match CFBD schools against -- supplements skipped")
        return saved

    if any_completed:
        try:
            entries = tag_team_ids(client.get_advanced_game_stats(season, week, season_type), school_to_id)
            if entries:
                put_json(cfbd_supplements.advanced_key(season, season_type, week), entries)
                saved["advanced"] = True
        except Exception:
            logger.exception("Failed fetching advanced game stats for season %s type %s week %s", season, season_type, week)

    for name, key, fetch in (
        ("talent", cfbd_supplements.talent_key(season), lambda: client.get_talent(season)),
        ("returning", cfbd_supplements.returning_key(season), lambda: client.get_returning_production(season)),
    ):
        try:
            saved[name] = _save_once(key, fetch, school_to_id, object_exists=object_exists, put_json=put_json)
        except Exception:
            logger.exception("Failed fetching %s for season %s", name, season)
    return saved
