"""
Jolpica-F1 (Ergast-compatible) HTTP client -- Formula 1's historical data
backbone. Unlike every other sport's client, this subclasses HttpClient
directly rather than a source-specific base class (library/http/espn.py's
EspnBaseClient) -- Jolpica shares nothing with ESPN's response envelope,
so there's no shared base to extend.

Base URL/response shape/rate limits confirmed live 2026-08-30:
https://api.jolpi.ca/ergast/f1/, Ergast-compatible JSON (an "MRData" root
object wrapping a RaceTable/StandingsTable/etc. per endpoint), `limit`/
`offset` pagination (default 30 rows/page, 100 max). Unauthenticated
rate limits: 4 req/s burst, 500 req/hr sustained -- DEFAULT_MIN_INTERVAL_
SECONDS uses the stricter sustained bound (3600/500 = 7.2s), which
trivially satisfies the burst limit too.

Base URL is https, not http -- confirmed live 2026-08-31 that a plain
http:// request 301-redirects to https:// (Cloudflare-enforced). requests
follows that redirect transparently, so a wrong http:// default wasn't a
functional bug, but it cost a real extra request against Jolpica's own
strict sustained-rate limit on every call, and left the request open to
having that redirect stripped/rewritten by anything positioned on-path
before the upgrade -- not a secrets leak (Jolpica is keyless, nothing
sensitive ever goes out), but a real, avoidable integrity gap for data
this project trains models on. Pointing straight at https:// removes
both problems outright.

A custom, non-default User-Agent is REQUIRED by Jolpica's own docs
(unlike ESPN, which just needs to not look like a bot). Same
env-var-with-a-real-default pattern as library/http/espn.py's own
_espn_root_url/_espn_user_agent (JOLPICA_API_ROOT_URL/JOLPICA_USER_AGENT,
backed by Terraform's jolpica_api_root_url/jolpica_user_agent variables),
so a UA or domain change is a Terraform/env var update, not a code change.

See github.com/jolpica/jolpica-f1/blob/main/docs/README.md and
rate_limits.md.
"""
import os

from library.http.client import HttpClient

DEFAULT_JOLPICA_API_ROOT_URL = "https://api.jolpi.ca/ergast/f1"
DEFAULT_JOLPICA_USER_AGENT = "sports-predictor-f1-client/1.0 (personal-use sports analytics; non-commercial)"
DEFAULT_MIN_INTERVAL_SECONDS = 3600 / 500  # 7.2s -- the stricter sustained-rate bound
PAGE_SIZE = 100  # Jolpica's own documented max rows/page


def _jolpica_root_url() -> str:
    return os.environ.get("JOLPICA_API_ROOT_URL", DEFAULT_JOLPICA_API_ROOT_URL).rstrip("/")


def _jolpica_user_agent() -> str:
    return os.environ.get("JOLPICA_USER_AGENT", DEFAULT_JOLPICA_USER_AGENT)


class JolpicaClient(HttpClient):
    def __init__(self, min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS):
        super().__init__(
            base_url=_jolpica_root_url(),
            min_interval_seconds=min_interval_seconds,
            user_agent=_jolpica_user_agent(),
        )

    def get_race_results(self, season: int, round_: int) -> dict:
        """One race weekend's full classification -- position, points,
        grid, laps, status, fastest lap, driver, constructor. Returns the
        raw Ergast envelope; MRData.RaceTable.Races is a single-element
        list (always exactly one, queried by an exact season+round), or
        empty if this round hasn't been run yet."""
        return self._get(f"{season}/{round_}/results.json", params={"limit": PAGE_SIZE})

    def get_qualifying(self, season: int, round_: int) -> dict:
        """Q1/Q2/Q3 times (modern 3-segment format, 2006+) or a single
        session time (pre-2006) plus grid position for one race weekend."""
        return self._get(f"{season}/{round_}/qualifying.json", params={"limit": PAGE_SIZE})

    def get_sprint(self, season: int, round_: int) -> dict:
        """Sprint-session result, same shape as get_race_results --
        only populated for a Sprint weekend (2021+); returns an empty
        Races list for a non-Sprint round rather than erroring."""
        return self._get(f"{season}/{round_}/sprint.json", params={"limit": PAGE_SIZE})

    def get_pitstops(self, season: int, round_: int) -> dict:
        """Every real pit stop this race -- driver, lap, stop number,
        time-of-day, duration. 2011+ only (Jolpica's own docs state data
        starts from the 2011 season)."""
        return self._get(f"{season}/{round_}/pitstops.json", params={"limit": PAGE_SIZE})

    def get_driver_standings(self, season: int, round_: int) -> dict:
        """Drivers' championship standings AS OF this round (not just
        season-end) -- position, points, wins, driver, current
        constructor(s)."""
        return self._get(f"{season}/{round_}/driverstandings.json", params={"limit": PAGE_SIZE})

    def get_constructor_standings(self, season: int, round_: int) -> dict:
        """Constructors' championship standings as of this round."""
        return self._get(f"{season}/{round_}/constructorstandings.json", params={"limit": PAGE_SIZE})

    def get_races(self, season: int) -> dict:
        """The season's full race calendar -- one entry per round, with
        circuit + practice/qualifying/race session dates/times. Plays the
        same "discover what's current" role get_scoreboard_for_date plays
        for every other sport's client, since Jolpica has no dedicated
        scoreboard-style endpoint of its own."""
        return self._get(f"{season}/races.json", params={"limit": PAGE_SIZE})

    def get_laps(self, season: int, round_: int) -> list[dict]:
        """Every lap, every driver, this race -- time + position, full
        history. High row volume (~20 drivers x ~55 laps, over the
        100-rows/page max for almost any real race), so this is the one
        endpoint this client paginates internally, returning the
        flattened per-driver Laps entries across every page rather than
        one raw envelope. Deferred to its own later backfill pass per the
        F1 onboarding plan (not pulled alongside results/qualifying/
        pitstops in the main backfill) -- provided here so that later
        pass has a ready-made client method rather than needing its own
        one-off pagination logic."""
        offset = 0
        laps: list[dict] = []
        while True:
            page = self._get(f"{season}/{round_}/laps.json", params={"limit": PAGE_SIZE, "offset": offset})
            races = page.get("MRData", {}).get("RaceTable", {}).get("Races", [])
            page_laps = races[0].get("Laps", []) if races else []
            if not page_laps:
                break
            laps.extend(page_laps)
            offset += PAGE_SIZE
            if offset >= int(page.get("MRData", {}).get("total", 0)):
                break
        return laps
