"""
The public agent's read tools, run by aws-lambdas/shared/assistant-public-
tools. Each one asks predict-read -- the same Lambda behind the app's own
routes -- for the data, so the assistant answers from exactly what the app
shows, then prunes the response: internal keys out, numbers rounded, lists
capped, a hard size limit on what reaches the model.

`read(resource, path_params, query_params)` returns predict-read's
(status_code, body) for an API-Gateway-shaped request.
"""
from __future__ import annotations

import json
from typing import Callable

from library.assistant import game_list, performance_overview, season_view
from library.assistant.settings import ToolSettings
from library.assistant.tool_specs import PUBLIC_TOOL_NAMES, validate

Reader = Callable[[str, dict, dict], tuple[int, dict]]
# sport -> that sport's live-score cache ({"fetched_at", "events"}), None when there is none.
LiveReader = Callable[[str], dict | None]

MAX_LIST = 16
# predict-read's 203 is a cached answer being refreshed in the background:
# still the app's current prediction.
OK = (200, 203)
MAX_HISTORY = 26

# Ids, cache bookkeeping and display-only fields: nothing an answer needs.
DROPPED_KEYS = {
    "entity_id", "event_key", "team_id", "model_versions", "model_version", "extra_fingerprint",
    "cached_at_epoch", "event_status", "color", "flag", "retry_after_seconds", "stale", "fingerprint",
    "venue_name", "venue_city", "venue_state", "simulations", "conference_brackets", "cup_bracket",
    "march_madness_bracket", "first_four", "bracket",
}


# Per-game player leaders: most of a game's size. A list of more than
# PLAYER_DETAIL_MAX_GAMES games leaves them out so the whole slate fits.
LIST_DROPPED_KEYS = frozenset({"leaders", "leaders_comparison"})
PLAYER_DETAIL_MAX_GAMES = 3


def prune(value, max_list: int = MAX_LIST, dropped: frozenset[str] = frozenset()):
    """Drops internal keys and empty values, rounds floats, caps lists, and
    turns a stored prediction ({"value", "model_version"}) into its value."""
    if isinstance(value, dict):
        kept = {k: prune(v, max_list, dropped) for k, v in value.items() if k not in DROPPED_KEYS | dropped and v is not None}
        return kept["value"] if set(kept) == {"value"} else kept
    if isinstance(value, list):
        return [prune(v, max_list, dropped) for v in value[:max_list]]
    if isinstance(value, float):
        return round(value, 3)
    return value


def _bounded(result: dict, settings: ToolSettings) -> dict:
    """Halves every list until the JSON fits settings.max_result_chars."""
    limit = settings.max_rows
    while len(json.dumps(result)) > settings.max_result_chars and limit > 1:
        limit //= 2
        result = prune(result, limit)
    if len(json.dumps(result)) > settings.max_result_chars:
        return {"error": "That answer is too large to return. Ask about a narrower part of it."}
    return result


def _unavailable(status: int, body: dict) -> dict:
    if status == 202:
        return {"pending": True, "message": "This prediction is being prepared. Try again in about a minute."}
    if status == 404:
        return {"error": "Not found. Check the sport and event id with list_events."}
    if status == 400:
        return {"error": str(body.get("error", "That request isn't supported."))[:200]}
    return {"error": f"The prediction service answered {status}.", "detail": str(body.get("error", ""))[:200]}


def _games(read: Reader, sport: str, status: str, date: str | None = None) -> tuple[list[dict] | None, dict | None]:
    query = {"status": status, **({"date": date} if date else {})}
    code, body = read(f"/{sport}/events", {}, query)
    return (body.get("events", []), None) if code in OK else (None, _unavailable(code, body))


def _dated_games(read: Reader, sport: str, date: str) -> tuple[list[dict] | None, dict | None]:
    """Every game on one date: the finished ones, then the rest."""
    found = []
    for status in ("completed", "scheduled"):
        events, error = _games(read, sport, status, date)
        if error:
            return None, error
        found += events
    return found, None


def _game_list(events: list[dict], settings: ToolSettings) -> dict:
    """Totals over every game, then the games themselves up to the cap;
    player detail only when the list is a few games."""
    shown = events[:settings.max_rows]
    dropped = frozenset() if len(events) <= PLAYER_DETAIL_MAX_GAMES else LIST_DROPPED_KEYS
    result = {"totals": game_list.totals(events), "count": len(events),
              "events": prune(game_list.with_flags(shown), settings.max_rows, dropped)}
    players = game_list.player_totals(events)
    if players:
        result["player_totals"] = players
    if len(shown) < len(events):
        result["truncated"] = f"Showing {len(shown)} of {len(events)} games; totals cover all of them. Filter by team or conference for the rest."
    return result


def list_events(read: Reader, args: dict, settings: ToolSettings) -> dict:
    when = args.get("when", "upcoming")
    if args.get("date"):
        events, error = _dated_games(read, args["sport"], args["date"])
    else:
        events, error = _games(read, args["sport"], "scheduled" if when == "upcoming" else "completed")
    if error:
        return error
    matching = game_list.filtered(events, args.get("team"), args.get("conference"), args.get("player"))
    period = {"date": args["date"]} if args.get("date") else {"when": when}
    result = {"sport": args["sport"], **period, **_game_list(matching, settings)}
    if len(matching) > PLAYER_DETAIL_MAX_GAMES:
        result["note"] = "Player predictions are left out of a list this long; filter by team, or get_event_prediction for one game."
    if args.get("conference") and not matching:
        result["conferences"] = game_list.conferences_seen(events)
    return _bounded(result, settings)


def get_live_scores(read: Reader, args: dict, settings: ToolSettings, live: LiveReader) -> dict:
    """Games in progress or just finished: the live-score cache the app's
    live view reads, joined to the day's games for names and predictions."""
    cache = live(args["sport"])
    if not cache or not cache.get("events"):
        return {"sport": args["sport"], "live": [], "note": "No live scores right now."}
    states = list(cache["events"].values())
    if not any("home_score" in state for state in states):
        # A field sport (golf, racing): its cache is already a leaderboard.
        return _bounded({"sport": args["sport"], "fetched_at": cache.get("fetched_at"), "leaderboards": prune(cache["events"], settings.max_rows)}, settings)
    events, error = _games(read, args["sport"], "scheduled")
    if error:
        return error
    games = game_list.live_games(events, cache)
    return _bounded({"sport": args["sport"], "fetched_at": cache.get("fetched_at"),
                     "in_progress": sum(1 for g in games if g["live"].get("live")),
                     "games": prune(games, settings.max_rows, LIST_DROPPED_KEYS)}, settings)


def only_player(value, player: str):
    """Keeps, in every list of named rows (a field, a leader list), only
    the rows whose name contains `player`."""
    wanted = player.strip().lower()
    if isinstance(value, dict):
        return {k: only_player(v, player) for k, v in value.items()}
    if isinstance(value, list):
        if value and all(isinstance(row, dict) and "name" in row for row in value):
            return [row for row in value if wanted in (row.get("name") or "").lower()]
        return [only_player(v, player) for v in value]
    return value


def get_event_prediction(read: Reader, args: dict, settings: ToolSettings) -> dict:
    resource = f"/{args['sport']}/predictions/events/{{event_id}}"
    code, body = read(resource, {"event_id": args["event_id"]}, {})
    if code not in OK:
        return _unavailable(code, body)
    result = body.get("result", body)
    if args.get("player"):
        result = only_player(result, args["player"])
    pruned = prune(result, settings.max_rows)
    if args.get("player") and "field" in result and not result["field"]:
        pruned["player_note"] = f"No player matching {args['player']!r} is in this event's field."
    if len(result.get("field") or []) > settings.max_rows:
        pruned["field_note"] = f"Top {settings.max_rows} of {len(result['field'])} by predicted finish; ask about a player by name for the rest."
    return _bounded({"sport": args["sport"], "event_id": args["event_id"], **pruned}, settings)


_PERFORMANCE_KEYS = ("model_name", "version", "kind", "season", "last_period", "history", "vs_baseline_pct", "bias", "bands", "best")


def _trim_model(model: dict) -> dict:
    entry = {k: model[k] for k in _PERFORMANCE_KEYS if model.get(k) is not None}
    if "history" in entry:
        entry["history"] = entry["history"][-MAX_HISTORY:]
    return entry


def get_model_performance(read: Reader, args: dict, settings: ToolSettings) -> dict:
    code, body = read(f"/{args['sport']}/model-performance", {}, {})
    if code not in OK:
        return _unavailable(code, body)
    models = body.get("models", [])
    wanted = args.get("model")
    chosen = [m for m in models if m.get("model_name") == wanted] if wanted else models
    if not chosen:
        return {"error": f"No model named {wanted!r}.", "models": sorted(m.get("model_name") for m in models)}
    return _bounded({
        "sport": args["sport"],
        "season": body.get("season"),
        "period_kind": body.get("period_kind"),
        "generated_at": body.get("generated_at"),
        "coverage": body.get("coverage"),
        "window_days": body.get("window_days"),
        "models": prune([_trim_model(m) for m in chosen], MAX_HISTORY),
        "by_conference": prune(body.get("by_conference")),
    }, settings)


BRACKET_NOTE = ("Each bracket shows one simulated run with the typical number of upsets, so a pick can go against "
                "the head-to-head chances (flag 'upset'); 'watch' marks a close matchup. round_odds are over every simulation.")


def get_season_projection(read: Reader, args: dict, settings: ToolSettings) -> dict:
    code, body = read(f"/{args['sport']}/season", {}, {})
    if code not in OK:
        return _unavailable(code, body)
    team, conference = args.get("team"), args.get("conference")
    base = {"sport": args["sport"], "season": body.get("season"), "generated_at": body.get("generated_at")}
    if args.get("view") == "bracket":
        view = season_view.brackets(body, team, conference, settings.max_rows)
        return _bounded({**base, **prune(view, settings.max_rows), "note": BRACKET_NOTE}, settings)
    return _bounded({**base, **prune(season_view.standings(body, team, conference, settings.max_rows), settings.max_rows)}, settings)


_MODEL_INFO_KEYS = ("model_name", "algorithm", "version", "trained_at", "accuracy", "log_loss", "naive_baseline_accuracy",
                    "rmse", "mae", "naive_baseline_rmse", "naive_baseline_mae", "candidates_ranked_by")
MAX_CANDIDATES = 3


def _model_info(card: dict) -> dict:
    entry = {k: card[k] for k in _MODEL_INFO_KEYS if card.get(k) is not None}
    entry["top_features"] = [f.get("feature") for f in card.get("top_features") or []]
    if card.get("candidates"):
        entry["candidates"] = card["candidates"][:MAX_CANDIDATES]
    return entry


def get_model_info(read: Reader, args: dict, settings: ToolSettings) -> dict:
    code, body = read(f"/{args['sport']}/models", {}, {})
    if code not in OK:
        return _unavailable(code, body)
    models = body.get("models", [])
    wanted = args.get("model")
    chosen = [m for m in models if m.get("model_name") == wanted] if wanted else models
    if not chosen:
        return {"error": f"No model named {wanted!r}.", "models": sorted(m.get("model_name") for m in models)}
    return _bounded({"sport": args["sport"], "count": len(chosen), "models": prune([_model_info(m) for m in chosen], settings.max_rows)}, settings)


TOOLS = {
    "list_events": list_events,
    "get_event_prediction": get_event_prediction,
    "get_model_performance": get_model_performance,
    "get_season_projection": get_season_projection,
    "get_model_info": get_model_info,
}


def run(name: str, args: dict | None, read: Reader, sports: list[str], settings: ToolSettings, live: LiveReader | None = None) -> dict:
    if name not in PUBLIC_TOOL_NAMES:
        raise ValueError(f"{name!r} is not a public tool")
    checked = validate(name, args, sports)
    if name == "get_performance_overview":
        return _bounded(prune(performance_overview.overview(read, sports, checked.get("model")), settings.max_rows), settings)
    if name == "get_live_scores":
        if live is None:
            raise ValueError("Live scores aren't available here.")
        return get_live_scores(read, checked, settings, live)
    return TOOLS[name](read, checked, settings)
