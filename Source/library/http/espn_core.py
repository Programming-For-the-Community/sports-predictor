"""
Client for ESPN's other, undocumented "core" API (sports.core.api.espn.com)
-- a different host and response shape than EspnBaseClient/site.api.espn.com's
single-response-with-everything-embedded style this project's NFLClient
already uses. This host paginates via `$ref` links: a list call returns
{"items": [{"$ref": "..."}, ...]}, and each item needs its own follow-up
GET to resolve into real data. Used for two things site.api.espn.com
doesn't expose at all: season-wide head coach info (experience, this
season's win rate with their current team) and per-team current injury
reports.

Depth charts are NOT here despite being coach/injury-adjacent -- ESPN's
core-API depth chart path 404s; the real one lives on site.api.espn.com
in the same single-response style everything else on NFLClient already
uses, so it's a plain new method there instead (library/http/nfl.py),
not on this client.
"""
import os
import re
from concurrent.futures import ThreadPoolExecutor

from library.http.client import HttpClient

DEFAULT_ESPN_CORE_API_ROOT_URL = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"

# One list call resolves to up to 32 (coaches) or a handful (injuries) $ref
# follow-ups -- parallelizing them is a meaningful speedup over resolving
# 30+ refs serially at min_interval_seconds apart, and RateLimiter is
# thread-safe (a shared lock around its own last-call timestamp), so every
# thread still paces its actual requests against the same interval, the
# same guarantee a backfill's concurrent workers already rely on.
DEREF_MAX_WORKERS = 10

_TRAILING_ID_RE = re.compile(r"/(\d+)(?:\?|$)")

# ESPN's injuries endpoint returns one entry per status change for the
# whole season, not just who's hurt right now (confirmed: a team with 6
# players genuinely out/questionable still returned 71 items, 65 of them
# "Active" -- recovered). Filtered to these three so get_team_injuries'
# output actually matches its own "current injury report" contract.
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
    layout (.../sports/<sport_path>/<endpoint>), which this host doesn't
    share (.../leagues/nfl/<endpoint> is already NFL-specific end to end,
    no separate sport_path concept)."""

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
        numbers (tenure in years; this REGULAR SEASON's win rate with this
        specific team; this coach's win rate across every postseason game
        of their whole career, any team), not computed in-house -- see the
        project's own design notes for why: no cold-start problem,
        available from day one, at the cost of a coach's win_pct possibly
        reflecting games this project's own completed-event history
        doesn't have (harmless: it's a feature input, not a stat this
        project claims to have derived itself). career_playoff_win_pct
        specifically exists ALONGSIDE season_win_pct, not instead of it --
        THIS season's postseason record alone would be a 1-3 game sample
        for the handful of teams that made the playoffs, and null for
        every regular-season game industry-wide (the postseason hasn't
        happened yet) -- a whole-career figure is non-null for every coach
        with any tenure and reflects a far larger, more stable sample."""
        listing = self._get(f"seasons/{season}/coaches", {"limit": 50})
        coaches = self._resolve_refs(listing.get("items", []))

        # Second round of $ref resolution -- each coach's own win-loss
        # record is itself a $ref, not embedded in the coach detail. A
        # coach whose team made the playoffs has TWO records entries, one
        # per season type ("/types/2/coaches/.../record" = regular season,
        # "/types/3/.../record" = postseason) -- confirmed live (Sean
        # McVay, 2025 Rams: regular season 12-5 = .706, postseason 1-1 =
        # .5). Filtered to type 2 specifically; an unfiltered dict
        # comprehension here used to just keep whichever entry happened to
        # come LAST in ESPN's own list, silently swapping in the
        # postseason record (or, in principle, a preseason one) instead of
        # "this season's win rate" the docstring above promises.
        record_refs = {
            coach["id"]: record["record"]["$ref"]
            for coach in coaches
            for record in coach.get("records", [])
            if "/types/2/coaches/" in (record.get("record", {}).get("$ref") or "")
        }
        with ThreadPoolExecutor(max_workers=min(len(record_refs) or 1, DEREF_MAX_WORKERS)) as executor:
            resolved_records = dict(zip(record_refs.keys(), executor.map(self.get_absolute, record_refs.values())))

        # Third round -- a coach's CAREER postseason record lives on their
        # own person-level resource (.../coaches/{id}/record/3, confirmed
        # live), a different id-scheme ("3" = Post Season) than the
        # per-season records above but the same shape. Built directly from
        # each coach's own id rather than resolving .../coaches/{id}'s own
        # careerRecords list first -- the record's own URL is entirely
        # predictable from coach_id, so this skips a whole extra round of
        # $ref resolution for the same result. get("value") is correctly
        # None (key absent, confirmed live) for a coach with zero career
        # playoff games, not a ZeroDivisionError or a 0.0 that would read
        # as "played and lost every one".
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
        historical-as-of-date equivalent exists (confirmed). ESPN's own
        endpoint returns the whole season's status-change log (up to 100,
        the largest team roster's seen so far), so this filters down to
        _CURRENT_INJURY_STATUSES before returning -- otherwise recovered
        players ("Active") would still show up here. Returns
        [{"entity_id", "status"}, ...], ESPN's raw status string
        unmapped -- severity thresholding is a feature-layer concern
        (library/features/nfl.py), not this client's."""
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
