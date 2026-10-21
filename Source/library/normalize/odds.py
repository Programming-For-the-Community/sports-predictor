"""
An event's pre-game betting line, stored on the event item as
`pregame_odds`: {"provider", "details", "favorite": "home"|"away"|None,
"home_moneyline", "away_moneyline"}. Parsed from either ESPN shape: the
scoreboard's competition["odds"] (upcoming games only -- ESPN drops it once
a game is under way) or the core API's /odds listing, which keeps the
closing line after the game.

`favorite` is the sportsbook's own flag, else the shorter moneyline; None
for a pick'em.

A line captured in the last minutes before kickoff (lock_event_odds) carries
`locked_at` and is the event's line for good: PipelineStorage writes nothing
over it.
"""
import posixpath
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from library.schema.keys import event_key

PREGAME_ODDS = "pregame_odds"
PREFERRED_PROVIDER = "draftkings"
LOCKED_AT = "locked_at"


def _moneyline(value) -> int | None:
    """American odds: -130, "+110", "EVEN" (+100). None for anything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        return None
    text = value.strip().upper()
    if text == "EVEN":
        return 100
    try:
        return int(text.lstrip("+"))
    except ValueError:
        return None


def implied_probability(moneyline: int) -> float:
    """American odds as a win chance, the sportsbook's margin included."""
    return -moneyline / (-moneyline + 100) if moneyline < 0 else 100 / (moneyline + 100)


def no_vig_home_chance(line: dict) -> float | None:
    """The home side's chance on a saved `pregame_odds` line with the
    sportsbook's margin taken out; None without both moneylines."""
    home, away = line.get("home_moneyline"), line.get("away_moneyline")
    if home is None or away is None:
        return None
    home_chance, away_chance = implied_probability(home), implied_probability(away)
    return home_chance / (home_chance + away_chance)


def _favorite(home_flag, away_flag, home_moneyline: int | None, away_moneyline: int | None) -> str | None:
    if home_flag and not away_flag:
        return "home"
    if away_flag and not home_flag:
        return "away"
    if home_moneyline is None or away_moneyline is None:
        return None
    home, away = implied_probability(home_moneyline), implied_probability(away_moneyline)
    if home == away:
        return None
    return "home" if home > away else "away"


def _pick(lines: list[dict]) -> dict | None:
    lines = [line for line in lines if isinstance(line, dict)]
    preferred = [
        line for line in lines
        if ((line.get("provider") or {}).get("name") or "").replace(" ", "").lower() == PREFERRED_PROVIDER
    ]
    return (preferred or lines or [None])[0]


def _odds(line: dict, home_moneyline: int | None, away_moneyline: int | None) -> dict | None:
    home = line.get("homeTeamOdds") or {}
    away = line.get("awayTeamOdds") or {}
    favorite = _favorite(home.get("favorite"), away.get("favorite"), home_moneyline, away_moneyline)
    if favorite is None and home_moneyline is None and not line.get("details"):
        return None
    return {
        "provider": (line.get("provider") or {}).get("name"),
        "details": line.get("details"),
        "favorite": favorite,
        "home_moneyline": home_moneyline,
        "away_moneyline": away_moneyline,
    }


def scoreboard_odds(competition: dict) -> dict | None:
    """From a scoreboard competition; the moneylines sit under
    odds[].moneyline.{home,away}.close.odds as strings."""
    line = _pick(competition.get("odds") or [])
    if line is None:
        return None
    moneyline = line.get("moneyline") or {}

    def side(name: str) -> int | None:
        quote = moneyline.get(name) or {}
        return _moneyline((quote.get("close") or quote.get("open") or {}).get("odds"))

    return _odds(line, side("home"), side("away"))


def core_odds(payload: dict) -> dict | None:
    """From the core API's /events/{id}/competitions/{id}/odds listing; the
    moneylines sit on each side as an int `moneyLine`. Carries the payload's
    `locked_at` when lock_event_odds saved it."""
    line = _pick(payload.get("items") or [])
    if line is None:
        return None
    odds = _odds(
        line,
        _moneyline((line.get("homeTeamOdds") or {}).get("moneyLine")),
        _moneyline((line.get("awayTeamOdds") or {}).get("moneyLine")),
    )
    if odds is not None and payload.get(LOCKED_AT):
        odds[LOCKED_AT] = payload[LOCKED_AT]
    return odds


def odds_key(sport: str, event_id: str) -> str:
    """Where ingest writes one event's core-API odds in the raw bucket."""
    return f"{sport}/odds/{event_id}.json"


def apply_core_odds(storage, sport: str, key: str, payload: dict, logger) -> None:
    """Saves an odds_key payload's line onto its event."""
    event_id = posixpath.splitext(posixpath.basename(key))[0]
    odds = core_odds(payload)
    if odds is None:
        logger.info("No line in %s", key)
    elif not storage.set_event_odds(event_key(sport, event_id), odds):
        logger.info("Event %s isn't on file yet or its line is locked -- line in %s not saved", event_id, key)


def fetch_event_odds(
    client, sport: str, event_ids, *, object_exists, put_json, logger, refresh: bool = False, max_workers: int = 1,
) -> tuple[int, int]:
    """Ingest side: saves each event's core-API odds to odds_key, (saved,
    failed). An event already saved is skipped unless `refresh` (a line
    still moving before the game). A listing with no line yet isn't saved,
    so the next run tries again."""
    to_fetch = [event_id for event_id in event_ids if refresh or not object_exists(odds_key(sport, event_id))]

    def fetch(event_id: str) -> bool | None:
        try:
            payload = client.get_event_odds(event_id)
            if not payload.get("items"):
                return False
            put_json(odds_key(sport, event_id), payload)
            return True
        except Exception:
            logger.exception("Failed saving odds for event %s", event_id)
            return None

    if not to_fetch:
        return 0, 0
    with ThreadPoolExecutor(max_workers=min(max_workers, len(to_fetch))) as executor:
        outcomes = list(executor.map(fetch, to_fetch))
    return outcomes.count(True), outcomes.count(None)


def lock_event_odds(client, sport: str, event_ids, *, put_json, logger, now: datetime | None = None) -> tuple[int, int]:
    """Kickoff side: saves each event's current core-API line to odds_key
    marked `locked_at`, which normalize then makes the event's line for good.
    (locked, failed); an event with no line listed counts as neither, so the
    scheduler's next attempt tries it again."""
    locked_at = (now or datetime.now(timezone.utc)).isoformat()
    locked = failed = 0
    for event_id in event_ids:
        try:
            payload = client.get_event_odds(event_id)
            if not payload.get("items"):
                logger.info("No line listed for event %s yet", event_id)
                continue
            put_json(odds_key(sport, event_id), {**payload, LOCKED_AT: locked_at})
            locked += 1
        except Exception:
            logger.exception("Failed locking the line for event %s", event_id)
            failed += 1
    return locked, failed
