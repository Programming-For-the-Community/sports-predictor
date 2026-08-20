"""
Client for ESPN's "core" API (sports.core.api.espn.com) -- a different
host and response shape than EspnBaseClient/site.api.espn.com's
single-response-with-everything-embedded style. This host paginates via
`$ref` links: a list call returns {"items": [{"$ref": "..."}, ...]}, and
each item needs its own follow-up GET to resolve into real data. Used for
season-wide head coach info (experience, this season's win rate with
their current team) and per-team current injury reports.
"""
import os
import re
from concurrent.futures import ThreadPoolExecutor

from library.http.client import HttpClient

DEFAULT_ESPN_CORE_API_ROOT_URL = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"

# Parallelizes $ref follow-up resolution; RateLimiter is thread-safe (a
# shared lock around its own last-call timestamp), so every thread still
# paces its actual requests against the same interval.
DEREF_MAX_WORKERS = 10

_TRAILING_ID_RE = re.compile(r"/(\d+)(?:\?|$)")

# ESPN's injuries endpoint returns one entry per status change for the
# whole season, not just who's hurt right now. Filtered to these three so
# get_team_injuries' output matches its own "current injury report"
# contract.
_CURRENT_INJURY_STATUSES = {"Questionable", "Doubtful", "Out"}


def _espn_core_root_url() -> str:
    return os.environ.get("ESPN_CORE_API_ROOT_URL", DEFAULT_ESPN_CORE_API_ROOT_URL).rstrip("/")


def _id_from_ref(ref_url: str | None) -> str | None:
    """Every ESPN core-API $ref ends in .../<numeric id>?lang=en&region=us
    -- this is the same raw id used everywhere else in this project
    (team_id, entity_id), so no separate id-mapping table is needed."""
    if not ref_url:
        return None
    match = _TRAILING_ID_RE.search(ref_url)
    return match.group(1) if match else None


class EspnCoreApiClient(HttpClient):
    """Extends HttpClient directly, not EspnBaseClient -- EspnBaseClient's
    sport_path/base_url shape is specific to site.api.espn.com's URL
    layout, which this host doesn't share."""

    def __init__(self, min_interval_seconds: float = 0.3):
        super().__init__(base_url=_espn_core_root_url(), min_interval_seconds=min_interval_seconds)

    def _resolve_refs(self, refs: list[dict]) -> list[dict]:
        urls = [ref["$ref"] for ref in refs if ref.get("$ref")]
        if not urls:
            return []
        with ThreadPoolExecutor(max_workers=min(len(urls), DEREF_MAX_WORKERS)) as executor:
            return list(executor.map(self.get_absolute, urls))

    def get_season_coaches(self, season: int) -> dict[str, dict]:
        """One head coach per team (32 items across the league) for
        `season`. Returns {team_id: {"coach_id", "coach_name",
        "experience", "season_win_pct", "career_playoff_win_pct"}} --
        team_id keys come from each coach's own team $ref, not a separate
        id lookup, so a caller never needs to know the coach->team mapping
        ahead of time.

        experience/season_win_pct/career_playoff_win_pct are ESPN's own
        numbers (tenure in years; this regular season's win rate with this
        specific team; this coach's win rate across every postseason game
        of their whole career, any team), not computed in-house.
        career_playoff_win_pct exists alongside season_win_pct: this
        season's postseason record alone would be a 1-3 game sample for
        the handful of teams that made the playoffs, and null for every
        regular-season game league-wide, while a whole-career figure is
        non-null for every coach with any tenure."""
        listing = self._get(f"seasons/{season}/coaches", {"limit": 50})
        coaches = self._resolve_refs(listing.get("items", []))

        # Second round of $ref resolution -- each coach's own win-loss
        # record is itself a $ref, not embedded in the coach detail. A
        # coach whose team made the playoffs has two records entries, one
        # per season type ("/types/2/coaches/.../record" = regular season,
        # "/types/3/.../record" = postseason). Filtered to type 2
        # specifically to match "this season's win rate".
        record_refs = {
            coach["id"]: record["record"]["$ref"]
            for coach in coaches
            for record in coach.get("records", [])
            if "/types/2/coaches/" in (record.get("record", {}).get("$ref") or "")
        }
        with ThreadPoolExecutor(max_workers=min(len(record_refs) or 1, DEREF_MAX_WORKERS)) as executor:
            resolved_records = dict(zip(record_refs.keys(), executor.map(self.get_absolute, record_refs.values())))

        # Third round -- a coach's career postseason record lives on their
        # own person-level resource (.../coaches/{id}/record/3, "3" = Post
        # Season), a different id-scheme than the per-season records above
        # but the same shape. Built directly from each coach's own id,
        # since the record's own URL is predictable from coach_id.
        # get("value") is None for a coach with zero career playoff games,
        # not a 0.0 that would read as "played and lost every one".
        career_postseason_refs = {
            coach["id"]: f"{self.base_url}/coaches/{coach['id']}/record/3"
            for coach in coaches if coach.get("id")
        }
        with ThreadPoolExecutor(max_workers=min(len(career_postseason_refs) or 1, DEREF_MAX_WORKERS)) as executor:
            resolved_career_postseason = dict(
                zip(career_postseason_refs.keys(), executor.map(self.get_absolute, career_postseason_refs.values())),
            )

        result: dict[str, dict] = {}
        for coach in coaches:
            team_id = _id_from_ref(coach.get("team", {}).get("$ref"))
            if team_id is None:
                continue
            record = resolved_records.get(coach.get("id"))
            career_postseason_record = resolved_career_postseason.get(coach.get("id"))
            result[team_id] = {
                "coach_id": coach.get("id"),
                "coach_name": f"{coach.get('firstName', '')} {coach.get('lastName', '')}".strip() or None,
                "experience": coach.get("experience"),
                "season_win_pct": record.get("value") if record else None,
                "career_playoff_win_pct": career_postseason_record.get("value") if career_postseason_record else None,
            }
        return result

    def get_team_injuries(self, team_id: str) -> list[dict]:
        """Current injury report for one team -- current-state only, no
        historical-as-of-date equivalent exists. ESPN's own endpoint
        returns the whole season's status-change log, so this filters
        down to _CURRENT_INJURY_STATUSES before returning, otherwise
        recovered players ("Active") would still show up here. Returns
        [{"entity_id", "status"}, ...], ESPN's raw status string
        unmapped -- severity thresholding is a feature-layer concern."""
        listing = self._get(f"teams/{team_id}/injuries", {"limit": 100})
        injuries = self._resolve_refs(listing.get("items", []))

        result = []
        for injury in injuries:
            status = injury.get("status")
            if status not in _CURRENT_INJURY_STATUSES:
                continue
            entity_id = _id_from_ref(injury.get("athlete", {}).get("$ref"))
            if entity_id is None:
                continue
            result.append({"entity_id": entity_id, "status": status})
        return result
