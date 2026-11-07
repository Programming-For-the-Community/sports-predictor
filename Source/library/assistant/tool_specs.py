"""
The assistant's tool definitions -- the one source both the agents (which
hand them to the model) and the tool Lambdas (which validate every call
against them) read. Built per call from the sport registry's sports
(library.assistant.sports), so a new sport needs no change here.

The public agent's role can invoke only the public tool Lambda, so the admin
tools are out of its reach whatever the model asks for; the admin agent may
call both sets.
"""
from __future__ import annotations

import re
from datetime import date

TRAFFIC_SOURCES = ["all", "waf_cloudfront", "waf_api", "api_gateway"]
MAX_STRING = 64

PUBLIC_TOOL_NAMES = frozenset({"list_events", "get_event_prediction", "get_model_performance", "get_season_projection"})
FAVORITE_TOOL_NAMES = frozenset({"save_favorite", "clear_favorites"})
ADMIN_TOOL_NAMES = frozenset({
    "get_cost_summary", "get_denied_locations", "get_repeat_offenders", "get_denied_endpoints",
    "get_denied_trend", "get_api_traffic", "get_lambda_health",
})

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE = {"type": "string", "description": "Date as YYYY-MM-DD (UTC)."}
_WEEK_BACK = "Defaults to 7 days ago."
_SOURCE = {"type": "string", "enum": TRAFFIC_SOURCES, "description": "Which log to count. Defaults to all."}


class ToolInputError(ValueError):
    """A tool call whose arguments don't match its definition."""


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
    }


def _range(default: str) -> dict:
    return {
        "start_date": {**_DATE, "description": f"First day included, YYYY-MM-DD (UTC). {default}"},
        "end_date": {**_DATE, "description": "Last day included, YYYY-MM-DD (UTC). Defaults to today."},
    }


def _public(sport: dict) -> list[dict]:
    return [
        _tool(
            "list_events",
            "List this app's games for a sport with each prediction: the next game day ('upcoming') or the most recent one ('recent', with results and whether each pick was right).",
            {"sport": sport, "when": {"type": "string", "enum": ["upcoming", "recent"], "description": "Which game day."}},
            ["sport", "when"],
        ),
        _tool(
            "get_event_prediction",
            "Get this app's prediction for one game: win probability, predicted margin and scores, and predicted player stat leaders.",
            {"sport": sport, "event_id": {"type": "string", "description": "Event id from list_events."}},
            ["sport", "event_id"],
        ),
        _tool(
            "get_model_performance",
            "Get how this app's models have performed this season: per model, the season result, the latest period, a period-by-period trend, and accuracy by confidence band.",
            {"sport": sport, "model": {"type": "string", "description": "Optional model name, e.g. win-probability. Omit for all models."}},
            ["sport"],
        ),
        _tool(
            "get_season_projection",
            "Get this app's season projection for a sport: projected records and playoff or title chances.",
            {"sport": sport},
            ["sport"],
        ),
    ]


def _favorites(sport: dict) -> list[dict]:
    return [
        _tool(
            "save_favorite",
            "Save one of the user's favourite teams, only when the user asks you to remember it.",
            {"sport": sport, "team": {"type": "string", "description": "Team abbreviation as this app shows it, e.g. DET."}},
            ["sport", "team"],
        ),
        _tool(
            "clear_favorites",
            "Remove the user's saved favourite teams, only when the user asks. Omit sport to clear all.",
            {"sport": sport},
            [],
        ),
    ]


def _admin() -> list[dict]:
    return [
        _tool(
            "get_cost_summary",
            "This project's AWS spend (Project-tagged only) for a date range, grouped one way, with the budget, the month forecast and the change against the previous period of the same length.",
            {**_range("Defaults to the first of this month."),
             "group_by": {"type": "string", "enum": ["service", "sport", "component", "sport_component"], "description": "Breakdown. Defaults to service."}},
            [],
        ),
        _tool("get_denied_locations", "Blocked or denied requests by client country for a date range.",
              {**_range(_WEEK_BACK), "source": _SOURCE}, []),
        _tool(
            "get_repeat_offenders",
            "Client IPs that were blocked repeatedly in a date range. An IP counts as serious when it reaches min_blocks or was blocked on min_days different days.",
            {**_range("Defaults to 30 days ago."), "source": _SOURCE,
             "min_blocks": {"type": "integer", "description": "Blocks needed to count as serious."},
             "min_days": {"type": "integer", "description": "Separate days needed to count as serious."}},
            [],
        ),
        _tool("get_denied_endpoints", "The paths blocked or denied requests asked for in a date range, each marked as a real app route or a probe.",
              {**_range(_WEEK_BACK), "source": _SOURCE}, []),
        _tool("get_denied_trend", "Blocked or denied requests over time by rule: hourly for short ranges, daily beyond.",
              {**_range(_WEEK_BACK), "source": _SOURCE}, []),
        _tool("get_api_traffic", "API Gateway requests, 4xx, 5xx and p95 latency for a date range (up to 455 days back), plus denied requests by route.",
              _range(_WEEK_BACK), []),
        _tool("get_lambda_health", "This project's Lambda functions with the most errors, throttles and timeouts in a date range (up to 455 days back).",
              _range(_WEEK_BACK), []),
    ]


def all_tools(sports: list[str]) -> list[dict]:
    sport = {"type": "string", "enum": list(sports), "description": "Sport key."}
    return _public(sport) + _favorites(sport) + _admin()


def anthropic_tools(names: frozenset[str] | set[str], sports: list[str]) -> list[dict]:
    """The definitions in the Messages API's tool shape, in a stable order so
    the prompt prefix caches. Arguments are checked by validate() where each
    tool runs."""
    return [t for t in all_tools(sports) if t["name"] in names]


def _check_value(key: str, prop: dict, value) -> None:
    if prop["type"] == "integer":
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ToolInputError(f"{key} must be a positive whole number")
        return
    if not isinstance(value, str) or len(value) > MAX_STRING:
        raise ToolInputError(f"{key} must be a short string")
    if "enum" in prop and value not in prop["enum"]:
        raise ToolInputError(f"{key} must be one of {prop['enum']}")
    if key.endswith("_date"):
        if not _DATE_PATTERN.match(value):
            raise ToolInputError(f"{key} must be YYYY-MM-DD")
        try:
            date.fromisoformat(value)
        except ValueError as error:
            raise ToolInputError(f"{key} is not a real date") from error


def validate(name: str, args: dict | None, sports: list[str]) -> dict:
    """Checks args against the tool's schema and returns them; raises
    ToolInputError on any mismatch, including keys the schema doesn't name."""
    tool = next((t for t in all_tools(sports) if t["name"] == name), None)
    if tool is None:
        raise ToolInputError(f"Unknown tool {name!r}")
    schema = tool["input_schema"]
    args = dict(args or {})
    unknown = set(args) - set(schema["properties"])
    if unknown:
        raise ToolInputError(f"Unknown argument(s) for {name}: {sorted(unknown)}")
    missing = [key for key in schema["required"] if args.get(key) in (None, "")]
    if missing:
        raise ToolInputError(f"Missing argument(s) for {name}: {missing}")
    for key, value in args.items():
        _check_value(key, schema["properties"][key], value)
    return args
