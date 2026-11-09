"""
Game-list helpers for the public tools: the totals the agent reports (worked
out over every game before the list is capped, so "how did the picks do"
never rests on a partial slate), team and conference filters, the app's
UPSET / UPSET WATCH flags, and live scores joined to the day's games.
"""
from __future__ import annotations

import re

# The app's rule (front-end/lib/core/models/upset.dart): only a betting
# favourite at -200 or shorter can be upset; UPSET = it lost, UPSET WATCH =
# not final yet and the model gives it 60% or less.
UPSET_MIN_FAVORITE_MONEYLINE = -200
UPSET_WATCH_MAX_FAVORITE_PERCENT = 60


def _home_win_probability(event: dict) -> float | None:
    win = ((event.get("prediction") or {}).get("predictions") or {}).get("win_probability") or {}
    return win.get("home_win_probability")


def _home_won(event: dict) -> bool | None:
    comparison = event.get("prediction_comparison") or {}
    if "actual_home_won" in comparison:
        return comparison["actual_home_won"]
    home = next((p for p in event.get("participants") or [] if p.get("role") == "home"), None)
    return (home or {}).get("result", {}).get("won") if event.get("status") == "completed" else None


def upset_flag(event: dict) -> str | None:
    favorite, moneyline = event.get("betting_favorite"), event.get("betting_favorite_moneyline")
    if favorite not in ("home", "away") or moneyline is None or moneyline > UPSET_MIN_FAVORITE_MONEYLINE:
        return None
    if event.get("status") == "completed":
        home_won = _home_won(event)
        return None if home_won is None or home_won == (favorite == "home") else "UPSET"
    chance = _home_win_probability(event)
    if chance is None:
        return None
    favorite_chance = chance if favorite == "home" else 1 - chance
    return "UPSET WATCH" if round(favorite_chance * 100) <= UPSET_WATCH_MAX_FAVORITE_PERCENT else None


_WORD = re.compile(r"[\w'.&-]+")


def _words(text: str | None) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


def names_team(team: str, abbreviation: str | None, name: str | None) -> bool:
    """`team` is the abbreviation, or whole words of the name in any order:
    "duke" is Duke, not the Duquesne Dukes; "minnesota gophers" is the
    Minnesota Golden Gophers."""
    wanted = team.strip().lower()
    if not wanted:
        return False
    if wanted == (abbreviation or "").lower():
        return True
    words = _words(wanted)
    return bool(words) and words <= _words(name)


def _matches_team(event: dict, team: str) -> bool:
    return any(names_team(team, p.get("abbreviation"), p.get("name")) for p in event.get("participants") or [])


def _matches_conference(event: dict, conference: str) -> bool:
    wanted = conference.strip().lower()
    return any(wanted in (p.get("conference") or "").lower() for p in event.get("participants") or [])


def _names_in(value) -> list[str]:
    """Every player name in a game's predicted leaders or its predicted-vs-actual."""
    if isinstance(value, dict):
        own = [value["name"]] if isinstance(value.get("name"), str) else []
        return own + [n for v in value.values() for n in _names_in(v)]
    if isinstance(value, list):
        return [n for v in value for n in _names_in(v)]
    return []


def _matches_player(event: dict, player: str) -> bool:
    leaders = [(event.get("prediction") or {}).get("leaders"), event.get("leaders_comparison")]
    return any(names_team(player, None, name) for name in _names_in(leaders))


def filtered(events: list[dict], team: str | None, conference: str | None, player: str | None = None) -> list[dict]:
    if player:
        events = [e for e in events if _matches_player(e, player)]
    if team:
        events = [e for e in events if _matches_team(e, team)]
    if conference:
        events = [e for e in events if _matches_conference(e, conference)]
    return events


def conferences_seen(events: list[dict]) -> list[str]:
    """For a conference filter that matched nothing: the names to try."""
    return sorted({p["conference"] for e in events for p in e.get("participants") or [] if p.get("conference")})


def totals(events: list[dict]) -> dict:
    """Counts over every game in the list, whatever the cap shows."""
    graded = [e["prediction_comparison"] for e in events if e.get("prediction_comparison")]
    correct = sum(1 for c in graded if c.get("correct"))
    flags = [upset_flag(e) for e in events]
    result = {"games": len(events), "with_prediction": sum(1 for e in events if e.get("prediction") or e.get("prediction_comparison"))}
    if graded:
        result.update(graded=len(graded), correct=correct, accuracy_pct=round(100 * correct / len(graded), 1))
    result.update(upsets=flags.count("UPSET"), upset_watch=flags.count("UPSET WATCH"))
    conferences = by_conference(events)
    if conferences:
        result["by_conference"] = conferences
    return result


def by_conference(events: list[dict]) -> list[dict]:
    """Graded picks per conference, most games first: a game counts once for
    each conference its teams are in, so a conference game counts once."""
    counts: dict[str, list[int]] = {}
    for event in events:
        comparison = event.get("prediction_comparison")
        if not comparison:
            continue
        for conference in {p["conference"] for p in event.get("participants") or [] if p.get("conference")}:
            tally = counts.setdefault(conference, [0, 0])
            tally[0] += 1
            tally[1] += 1 if comparison.get("correct") else 0
    rows = [{"conference": c, "graded": g, "correct": r, "accuracy_pct": round(100 * r / g, 1)} for c, (g, r) in counts.items()]
    return sorted(rows, key=lambda row: (-row["graded"], row["conference"]))


def with_flags(events: list[dict]) -> list[dict]:
    return [{**e, "upset": flag} if (flag := upset_flag(e)) else e for e in events]


def live_games(events: list[dict], live: dict) -> list[dict]:
    """The day's games that are in progress or just finished, each with its
    live score and clock joined from the live-score cache (keyed by event id)."""
    states = live.get("events") or {}
    joined = []
    for event in events:
        state = states.get(event.get("event_id"))
        if state and (state.get("live") or state.get("completed")):
            joined.append({**event, "live": {k: state.get(k) for k in ("live", "completed", "detail", "home_score", "away_score", "situation") if state.get(k) is not None}})
    return joined


BIGGEST_MISSES = 3


def _player_entries(value) -> list[dict]:
    """Every predicted-vs-actual player entry ({name, predicted, actual}) in a game's leaders_comparison."""
    if isinstance(value, dict):
        if isinstance(value.get("predicted"), dict) and isinstance(value.get("actual"), dict):
            return [value]
        return [e for v in value.values() for e in _player_entries(v)]
    if isinstance(value, list):
        return [e for v in value for e in _player_entries(v)]
    return []


def _graded_players(event: dict):
    """(team abbreviation, entry) for each predicted-vs-actual player entry,
    the team being the side of the game the entry is listed under."""
    comparison = event.get("leaders_comparison") or {}
    for side in ("home", "away"):
        team = next((p.get("abbreviation") for p in event.get("participants") or [] if p.get("role") == side), None)
        for entry in _player_entries(comparison.get(side)):
            yield team, entry


def player_totals(events: list[dict], stat: str | None = None, max_rows: int = 100) -> list[dict]:
    """Per stat, over every game's listed players with an actual: how many
    were graded, the average miss, the average lean (+ = predicted too high)
    and the biggest misses. With `stat`, only that stat, and every graded
    player in it (players: name, team, predicted, actual), biggest miss first."""
    graded: dict[str, list[dict]] = {}
    for event in events:
        for team, entry in _graded_players(event):
            for name, actual in entry["actual"].items():
                predicted = entry["predicted"].get(name)
                if (stat is None or name == stat) and isinstance(predicted, (int, float)) and isinstance(actual, (int, float)):
                    graded.setdefault(name, []).append({"player": entry.get("name") or "?", "team": team,
                                                        "predicted": round(predicted, 1), "actual": actual, "miss": round(predicted - actual, 1)})
    rows = []
    for name, players in sorted(graded.items()):
        players.sort(key=lambda p: -abs(p["miss"]))
        row = {"stat": name, "graded": len(players),
               "avg_miss": round(sum(abs(p["miss"]) for p in players) / len(players), 1),
               "avg_lean": round(sum(p["miss"] for p in players) / len(players), 1)}
        if stat:
            row["players"] = players[:max_rows]
        else:
            row["biggest_misses"] = [{k: p[k] for k in ("player", "team", "predicted", "actual")} for p in players[:BIGGEST_MISSES]]
        rows.append(row)
    return rows
