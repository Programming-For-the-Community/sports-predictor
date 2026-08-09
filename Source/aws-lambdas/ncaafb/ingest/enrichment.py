"""
Coach/ranking enrichment for ingest/handler.py's game writes -- ingest-
only, unlike venue_indoor (library/storage/ncaafb_team_cache.py, shared
with schedule-sync -- see that module's own docstring for why). No injury
or depth-chart equivalent exists for college football (see project plan),
so enrich_games below only ever attaches coach info and current AP rank
on top of what attach_venue_indoor already provides.

Unlike NFL's own ingest/enrichment.py, CFBD's /coaches and /rankings
responses key teams by school name, not a numeric id, while /games only
has numeric home_id/away_id -- ncaafb_team_cache.get_cached_teams' season-
scoped /teams list is what bridges the two (school name <-> id) for both
lookups below. The lookup builders and coach cache themselves live in
library/storage/ncaafb_coach_cache.py (data-backfills/ncaafb/backfill.py
needs the exact same season-scoped resolution for 10 seasons of
historical games) -- this module keeps only the per-week orchestration.
"""
import logging

from library.http.cfbd import CFBDClient
from library.storage.ncaafb_coach_cache import coach_lookup_by_school, get_cached_coaches, rank_lookup_by_school
from library.storage.ncaafb_team_cache import attach_venue_indoor, get_cached_teams, teams_by_id

logger = logging.getLogger("ncaafb-ingest")


def enrich_games(games: list[dict], season: int, week: int, client: CFBDClient, s3, bucket: str) -> None:
    """Attaches venue_indoor (see ncaafb_team_cache.attach_venue_indoor),
    home_coach/away_coach, and home_current_rank/away_current_rank to
    each game dict in place, before that week's games are written to S3.
    Best-effort: a coach/ranking fetch failure is logged and those fields
    are simply omitted, matching NFL's enrich_events convention.

    Rankings are fetched fresh every call, unlike teams/coaches -- they
    change weekly and this is a single bulk call scoped to one week, not
    a per-team fan-out, so there's no TTL cache to gain from. This
    function only ever runs for ONE week (the current one) per ingest
    run -- exactly why schedule-sync's own season-wide walk calls
    attach_venue_indoor directly instead of this function, rather than
    re-fetching rankings once per week of the season."""
    try:
        attach_venue_indoor(games, season, client, s3, bucket)
    except Exception:
        logger.exception("Failed fetching season teams for %d -- venue_indoor will be omitted", season)

    try:
        by_id = teams_by_id(get_cached_teams(s3, bucket, client, season))
    except Exception:
        logger.exception("Failed fetching season teams for %d -- coach/rank fields will be omitted", season)
        by_id = {}

    try:
        coach_by_school = coach_lookup_by_school(get_cached_coaches(s3, bucket, client, season), season)
    except Exception:
        logger.exception("Failed fetching season coaches for %d -- coach fields will be omitted", season)
        coach_by_school = {}

    try:
        rank_by_school = rank_lookup_by_school(client.get_rankings(season, week=week))
    except Exception:
        logger.exception("Failed fetching rankings for season %d week %d -- rank fields will be omitted", season, week)
        rank_by_school = {}

    for game in games:
        home_id = str(game["homeId"]) if game.get("homeId") is not None else None
        away_id = str(game["awayId"]) if game.get("awayId") is not None else None
        home_school = by_id.get(home_id, {}).get("school") if home_id else None
        away_school = by_id.get(away_id, {}).get("school") if away_id else None

        game["home_coach"] = coach_by_school.get(home_school)
        game["away_coach"] = coach_by_school.get(away_school)
        game["home_current_rank"] = rank_by_school.get(home_school)
        game["away_current_rank"] = rank_by_school.get(away_school)
