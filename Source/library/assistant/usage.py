"""
Daily assistant usage, one row per agent per day in the assistant table, for
the admin agent's get_assistant_usage:

  pk=usage#<agent>  sk=<yyyy-mm-dd>  questions, one count per outcome,
                                     input/output tokens, tool calls, and
                                     the set of users who asked (TTL'd)

The agent adds each finished question; a failed write is logged and never
fails the answer.
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta

from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)

USAGE_TTL_DAYS = 400
OUTCOMES = ("answered", "blocked", "quota", "session_full", "failed")


def _pk(agent: str) -> str:
    return f"usage#{agent}"


def record(table, agent: str, day: date, sub: str, outcome: str, input_tokens: int = 0, output_tokens: int = 0, tool_calls: int = 0) -> None:
    if outcome not in OUTCOMES:
        return
    expires = int(time.mktime((day + timedelta(days=USAGE_TTL_DAYS)).timetuple()))
    try:
        table.update_item(
            Key={"pk": _pk(agent), "sk": day.isoformat()},
            UpdateExpression="ADD questions :one, #outcome :one, input_tokens :input, output_tokens :output, tool_calls :tools, #users :user "
                             "SET expires_at = :expires",
            ExpressionAttributeNames={"#outcome": outcome, "#users": "users"},
            ExpressionAttributeValues={":one": 1, ":input": input_tokens, ":output": output_tokens, ":tools": tool_calls,
                                       ":user": {sub}, ":expires": expires},
        )
    except Exception:
        logger.exception("Usage not recorded for %s", agent)


COUNTED = ("questions", *OUTCOMES, "input_tokens", "output_tokens", "tool_calls")
AGENTS = ("public", "admin")


def _totals(rows: list[dict]) -> dict:
    totals = {k: sum(r[k] for r in rows) for k in COUNTED}
    totals["user_days"] = sum(r["users"] for r in rows)
    if totals["questions"]:
        totals["tool_calls_per_question"] = round(totals["tool_calls"] / totals["questions"], 2)
    return totals


def report(table, args: dict, start: date, end: date, max_rows: int) -> dict:
    """Totals and daily rows per agent for the admin agent's get_assistant_usage."""
    agents = [args["agent"]] if args.get("agent") else list(AGENTS)
    result = {"start_date": start.isoformat(), "end_date": end.isoformat(), "agents": {}}
    for agent in agents:
        rows = read(table, agent, start, end)
        result["agents"][agent] = {"totals": _totals(rows), "days": rows[-max_rows:]}
    result["note"] = ("Days are U.S. Eastern. user_days adds each day's distinct users, so one person on three days counts three. "
                      "Model and Bedrock spend is in the cost summary.")
    return result


def read(table, agent: str, start: date, end: date) -> list[dict]:
    """The agent's daily rows from start to end, oldest first; users is the
    count of distinct users that day."""
    items = table.query(KeyConditionExpression=Key("pk").eq(_pk(agent)) & Key("sk").between(start.isoformat(), end.isoformat())).get("Items", [])
    rows = []
    for item in items:
        row = {"date": item["sk"], "users": len(item.get("users") or ())}
        row.update({k: int(item.get(k, 0)) for k in COUNTED})
        rows.append(row)
    return rows
