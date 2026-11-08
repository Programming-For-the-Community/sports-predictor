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

from library.assistant.settings import ToolSettings
from library.assistant.tool_specs import PUBLIC_TOOL_NAMES, validate

Reader = Callable[[str, dict, dict], tuple[int, dict]]

MAX_LIST = 16
MAX_HISTORY = 26

# Ids, cache bookkeeping and display-only fields: nothing an answer needs.
DROPPED_KEYS = {
    "entity_id", "event_key", "team_id", "model_versions", "model_version", "extra_fingerprint",
    "cached_at_epoch", "event_status", "color", "flag", "retry_after_seconds", "stale", "fingerprint",
    "venue_name", "venue_city", "venue_state", "simulations", "conference_brackets", "cup_bracket",
    "march_madness_bracket", "first_four", "bracket",
}


# Per-game player leaders: most of a game's size. A list of games leaves them
# out so the whole slate fits; get_event_prediction has them.
LIST_DROPPED_KEYS = frozenset({"leaders", "leaders_comparison"})


def prune(value, max_list: int = MAX_LIST, dropped: frozenset[str] = frozenset()):
    if isinstance(value, dict):
        return {k: prune(v, max_list, dropped) for k, v in value.items() if k not in DROPPED_KEYS | dropped and v is not None}
    if isinstance(value, list):
        return [prune(v, max_list, dropped) for v in value[:max_list]]
    if isinstance(value, float):
        return round(value, 3)
    return value


def _bounded(result: dict, settings: ToolSettings) -> dict:
    """Halves every list until the JSON fits settings.max_result_chars."""
    limit = MAX_LIST
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
    return {"error": f"The prediction service answered {status}.", "detail": str(body.get("error", ""))[:200]}


def list_events(read: Reader, args: dict, settings: ToolSettings) -> dict:
    status = "scheduled" if args["when"] == "upcoming" else "completed"
    code, body = read("/{sport}/events".replace("{sport}", args["sport"]), {}, {"status": status})
    if code != 200:
        return _unavailable(code, body)
    events = body.get("events", [])
    return _bounded({
        "sport": args["sport"], "when": args["when"], "count": len(events),
        "events": prune(events[:settings.max_rows], settings.max_rows, LIST_DROPPED_KEYS),
        "note": "Player leaders are left out here; get_event_prediction has them for one game.",
    }, settings)


def get_event_prediction(read: Reader, args: dict, settings: ToolSettings) -> dict:
    resource = f"/{args['sport']}/predictions/events/{{event_id}}"
    code, body = read(resource, {"event_id": args["event_id"]}, {})
    if code != 200:
        return _unavailable(code, body)
    return _bounded({"sport": args["sport"], "event_id": args["event_id"], **prune(body.get("result", body))}, settings)


_PERFORMANCE_KEYS = ("model_name", "version", "kind", "season", "last_period", "history", "vs_baseline_pct", "bias", "bands", "best")


def _trim_model(model: dict) -> dict:
    entry = {k: model[k] for k in _PERFORMANCE_KEYS if model.get(k) is not None}
    if "history" in entry:
        entry["history"] = entry["history"][-MAX_HISTORY:]
    return entry


def get_model_performance(read: Reader, args: dict, settings: ToolSettings) -> dict:
    code, body = read(f"/{args['sport']}/model-performance", {}, {})
    if code != 200:
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


def get_season_projection(read: Reader, args: dict, settings: ToolSettings) -> dict:
    code, body = read(f"/{args['sport']}/season", {}, {})
    if code != 200:
        return _unavailable(code, body)
    return _bounded({"sport": args["sport"], **prune(body)}, settings)


TOOLS = {
    "list_events": list_events,
    "get_event_prediction": get_event_prediction,
    "get_model_performance": get_model_performance,
    "get_season_projection": get_season_projection,
}


def run(name: str, args: dict | None, read: Reader, sports: list[str], settings: ToolSettings) -> dict:
    if name not in PUBLIC_TOOL_NAMES:
        raise ValueError(f"{name!r} is not a public tool")
    return TOOLS[name](read, validate(name, args, sports), settings)
