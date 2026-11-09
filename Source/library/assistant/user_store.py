"""
Per-user assistant state in the assistant DynamoDB table
(Terraform/dynamodb-assistant.tf), keyed by the caller's Cognito sub:

  pk=<sub>  sk=quota#<agent>#<yyyy-mm-dd>   questions asked that day (TTL'd)
  pk=<sub>  sk=favorites                    {"teams": {"nfl": ["DET"], "pga": ["Scottie Scheffler"]}}

One favourite per registered sport: a team's abbreviation, or for a field
sport (golf, racing) the competitor's name. Saving another for the same sport
replaces it.

The sub always comes from the verified token, never from the request body.
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta, timezone

from botocore.exceptions import ClientError

QUOTA_TTL_DAYS = 2
_TEAM = re.compile(r"^[A-Z0-9&]{2,6}$")
_COMPETITOR = re.compile(r"^[^\W\d_][\w'. -]{1,48}$")


class FavoriteError(ValueError):
    """A favourite the store refuses: unknown sport, or a malformed team or name."""


def take_question(table, sub: str, agent: str, daily_limit: int, today: date) -> tuple[bool, int]:
    """Counts one question against today's limit. Returns (allowed, remaining)."""
    expires = int(time.mktime((today + timedelta(days=QUOTA_TTL_DAYS)).timetuple()))
    try:
        response = table.update_item(
            Key={"pk": sub, "sk": f"quota#{agent}#{today.isoformat()}"},
            UpdateExpression="ADD asked :one SET expires_at = :expires",
            ConditionExpression="attribute_not_exists(asked) OR asked < :limit",
            ExpressionAttributeValues={":one": 1, ":limit": daily_limit, ":expires": expires},
            ReturnValues="UPDATED_NEW",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False, 0
        raise
    return True, max(0, daily_limit - int(response["Attributes"]["asked"]))


def questions_left(table, sub: str, agent: str, daily_limit: int, today: date) -> int:
    item = table.get_item(Key={"pk": sub, "sk": f"quota#{agent}#{today.isoformat()}"}).get("Item") or {}
    return max(0, daily_limit - int(item.get("asked", 0)))


def get_favorites(table, sub: str) -> dict[str, list[str]]:
    item = table.get_item(Key={"pk": sub, "sk": "favorites"}).get("Item") or {}
    return {sport: list(teams) for sport, teams in (item.get("teams") or {}).items() if teams}


def _put_favorites(table, sub: str, teams: dict[str, list[str]]) -> None:
    table.put_item(Item={
        "pk": sub, "sk": "favorites", "teams": teams,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })


def _favorite_value(sport: str, team: str, field_sports: frozenset[str]) -> str:
    if sport in field_sports:
        name = " ".join(team.split())
        if not _COMPETITOR.match(name):
            raise FavoriteError("Use the player's or driver's name as the app shows it, e.g. Scottie Scheffler")
        return name
    abbreviation = team.strip().upper()
    if not _TEAM.match(abbreviation):
        raise FavoriteError("Use the team's abbreviation as the app shows it, e.g. DET")
    return abbreviation


def save_favorite(table, sub: str, sport: str, team: str, sports: list[str], field_sports: frozenset[str] = frozenset()) -> dict[str, list[str]]:
    """sports: the registry's sport keys; field_sports: those whose favourite
    is a competitor (library.assistant.sports). Replaces the sport's earlier one."""
    if sport not in sports:
        raise FavoriteError(f"Unknown sport {sport!r}")
    value = _favorite_value(sport, team, field_sports)
    teams = get_favorites(table, sub)
    if teams.get(sport) == [value]:
        return teams
    teams[sport] = [value]
    _put_favorites(table, sub, teams)
    return teams


def remove_favorite(table, sub: str, sport: str, team: str) -> dict[str, list[str]]:
    teams = get_favorites(table, sub)
    remaining = [t for t in teams.get(sport, []) if t.casefold() != " ".join(team.split()).casefold()]
    if remaining:
        teams[sport] = remaining
    else:
        teams.pop(sport, None)
    _put_favorites(table, sub, teams)
    return teams


def clear_favorites(table, sub: str, sport: str | None = None) -> dict[str, list[str]]:
    if sport is None:
        table.delete_item(Key={"pk": sub, "sk": "favorites"})
        return {}
    teams = get_favorites(table, sub)
    teams.pop(sport, None)
    _put_favorites(table, sub, teams)
    return teams
