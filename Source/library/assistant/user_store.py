"""
Per-user assistant state in the assistant DynamoDB table
(Terraform/dynamodb-assistant.tf), keyed by the caller's Cognito sub:

  pk=<sub>  sk=quota#<agent>#<yyyy-mm-dd>   questions asked that day (TTL'd)
  pk=<sub>  sk=favorites                    {"teams": {"nfl": ["DET"], ...}}

The sub always comes from the verified token, never from the request body.
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta, timezone

from botocore.exceptions import ClientError

QUOTA_TTL_DAYS = 2
MAX_FAVORITE_SPORTS = 3
MAX_TEAMS_PER_SPORT = 2
_TEAM = re.compile(r"^[A-Z0-9&]{2,6}$")


class FavoriteError(ValueError):
    """A favourite the store refuses: unknown sport, malformed team, or over the limits."""


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


def save_favorite(table, sub: str, sport: str, team: str, sports: list[str]) -> dict[str, list[str]]:
    """sports: the registry's sport keys (library.assistant.sports)."""
    team = team.strip().upper()
    if sport not in sports:
        raise FavoriteError(f"Unknown sport {sport!r}")
    if not _TEAM.match(team):
        raise FavoriteError("Use the team's abbreviation as the app shows it, e.g. DET")
    teams = get_favorites(table, sub)
    current = teams.get(sport, [])
    if team in current:
        return teams
    if sport not in teams and len(teams) >= MAX_FAVORITE_SPORTS:
        raise FavoriteError(f"Up to {MAX_FAVORITE_SPORTS} sports can be saved; remove one first")
    if len(current) >= MAX_TEAMS_PER_SPORT:
        raise FavoriteError(f"Up to {MAX_TEAMS_PER_SPORT} teams per sport can be saved; remove one first")
    teams[sport] = current + [team]
    _put_favorites(table, sub, teams)
    return teams


def remove_favorite(table, sub: str, sport: str, team: str) -> dict[str, list[str]]:
    teams = get_favorites(table, sub)
    remaining = [t for t in teams.get(sport, []) if t != team.strip().upper()]
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
