"""
An event's pre-game betting line, stored on the event item as
`pregame_odds`: {"provider", "details", "favorite": "home"|"away"|None,
"home_moneyline", "away_moneyline"}. Parsed from either ESPN shape: the
scoreboard's competition["odds"] (upcoming games only -- ESPN drops it once
a game is under way) or the core API's /odds listing, which keeps the
closing line after the game.

`favorite` is the sportsbook's own flag, else the shorter moneyline; None
for a pick'em.
"""
import posixpath
from concurrent.futures import ThreadPoolExecutor

from library.schema.keys import event_key

PREGAME_ODDS = "pregame_odds"
PREFERRED_PROVIDER = "draftkings"


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


def _implied(moneyline: int) -> float:
    return -moneyline / (-moneyline + 100) if moneyline < 0 else 100 / (moneyline + 100)


def _favorite(home_flag, away_flag, home_moneyline: int | None, away_moneyline: int | None) -> str | None:
    if home_flag and not away_flag:
        return "home"
    if away_flag and not home_flag:
        return "away"
    if home_moneyline is None or away_moneyline is None:
        return None
    home, away = _implied(home_moneyline), _implied(away_moneyline)
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
    moneylines sit on each side as an int `moneyLine`."""
    line = _pick(payload.get("items") or [])
    if line is None:
        return None
    return _odds(
        line,
        _moneyline((line.get("homeTeamOdds") or {}).get("moneyLine")),
        _moneyline((line.get("awayTeamOdds") or {}).get("moneyLine")),
    )


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
        logger.warning("Event %s isn't on file yet -- line in %s not saved", event_id, key)


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
