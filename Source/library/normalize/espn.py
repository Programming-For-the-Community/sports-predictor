"""
ESPN-response-to-project-schema normalizers shared across every sport that
uses the ESPN site API. Each function accepts the raw ESPN dict plus the
caller's sport string so the same logic works for NFL, NBA, NCAA, etc.

Sport-specific behaviour -- such as which stat keys pack two numbers into
one string -- is passed in by the caller via compound_key_splits rather
than hardcoded here.
"""
from library.parsing import parse_number, snake_case
from library.schema.keys import entity_key, event_key, player_key, team_key


def team_to_entity(team: dict, sport: str) -> dict:
    team_id = str(team["id"])
    return {
        "entity_key": entity_key(sport, team_id),
        "entity_id": team_id,
        "sport": sport,
        "entity_type": "team",
        "name": team.get("displayName", team_id),
        "metadata": {
            "abbreviation": team.get("abbreviation"),
            "location": team.get("location"),
            "nickname": team.get("nickname") or team.get("name"),
        },
    }


def _event_status(status: dict) -> str:
    return "completed" if status.get("type", {}).get("completed") else "scheduled"


def scoreboard_event_to_event_item(event: dict, sport: str) -> dict:
    competition = event["competitions"][0]
    participants = []
    for competitor in competition["competitors"]:
        participants.append({
            "entity_id": str(competitor["team"]["id"]),
            "role": competitor.get("homeAway", "unknown"),
            "result": {
                "score": parse_number(competitor.get("score", "0")),
                "won": bool(competitor.get("winner", False)),
            },
        })
    event_id = event["id"]
    # venue/weather already come back on the same scoreboard response
    # ingest already fetches -- no extra API call. weather is frequently
    # null (most reliably for indoor games, where it doesn't apply), so
    # this is a real but partial signal, not a guaranteed one.
    venue = competition.get("venue") or {}
    venue_address = venue.get("address") or {}
    weather = competition.get("weather") or {}
    return {
        "event_key": event_key(sport, event_id),
        "event_id": event_id,
        "sport": sport,
        "event_type": "head_to_head",
        "event_date": event["date"][:10],
        "status": _event_status(event.get("status", {})),
        "participants": participants,
        "season": event["season"]["year"],
        "season_type": event["season"]["type"],
        "week": event.get("week", {}).get("number"),
        "venue_indoor": venue.get("indoor"),
        "venue_city": venue_address.get("city"),
        "venue_state": venue_address.get("state"),
        "weather_temperature": weather.get("temperature"),
    }


def boxscore_to_player_game_stats(
    summary: dict,
    sport: str,
    compound_key_splits: dict[str, tuple[str, str]],
) -> tuple[list[dict], list[dict]]:
    """Returns (player_game_stats items, player entity items) for one game.

    compound_key_splits maps an ESPN stat key whose value packs two numbers
    into one string (e.g. "24/31") onto a pair of output field names. Keys
    absent from the map are snake-cased and stored as-is. A single athlete
    appearing in multiple stat categories is merged into one stat_line.
    """
    header = summary["header"]
    event_id = header["id"]
    event_date = None
    for competition in header.get("competitions", []):
        if competition.get("date"):
            event_date = competition["date"][:10]
            break

    stat_lines: dict[str, dict] = {}
    athlete_meta: dict[str, tuple] = {}
    athlete_team: dict[str, str] = {}

    for team_block in summary.get("boxscore", {}).get("players", []):
        team_id = str(team_block["team"]["id"])
        for category in team_block.get("statistics", []):
            category_name = snake_case(category.get("name", "misc"))
            keys = category.get("keys", [])
            for athlete_entry in category.get("athletes", []):
                athlete = athlete_entry["athlete"]
                athlete_id = athlete["id"]
                values = athlete_entry.get("stats", [])
                line = stat_lines.setdefault(athlete_id, {})
                athlete_team[athlete_id] = team_id
                athlete_meta[athlete_id] = (athlete.get("displayName", ""), athlete.get("jersey"))
                for key, value in zip(keys, values):
                    if key in compound_key_splits:
                        first_name, second_name = compound_key_splits[key]
                        sep = "/" if "/" in value else "-"
                        parts = value.split(sep, 1)
                        if len(parts) == 2:
                            line[first_name] = parse_number(parts[0])
                            line[second_name] = parse_number(parts[1])
                            continue
                    # Most ESPN stat keys already bake their category into
                    # the name itself (category "passing", key
                    # "passingYards") -- snake-casing that and then also
                    # prefixing the category would double it up into
                    # "passing_passing_yards". Only prefix when the key
                    # doesn't already carry it, so a bare key that would
                    # otherwise collide across categories (category
                    # "interceptions"'s own "interceptions" key vs
                    # "passing"'s "interceptions" key, defensive picks vs
                    # thrown picks) still gets disambiguated.
                    snake_key = snake_case(key)
                    if snake_key == category_name or snake_key.startswith(f"{category_name}_"):
                        field_name = snake_key
                    else:
                        field_name = f"{category_name}_{snake_key}"
                    line[field_name] = parse_number(value)

    player_game_stats_items = []
    player_entities = []
    for athlete_id, line in stat_lines.items():
        display_name, jersey = athlete_meta[athlete_id]
        team_id = athlete_team[athlete_id]
        player_game_stats_items.append({
            "event_key": event_key(sport, event_id),
            "player_key": player_key(sport, athlete_id),
            "entity_id": athlete_id,
            "team_id": team_id,
            "event_date": event_date,
            "stat_line": line,
        })
        player_entities.append({
            "entity_key": entity_key(sport, athlete_id),
            "entity_id": athlete_id,
            "sport": sport,
            "entity_type": "player",
            "name": display_name,
            "metadata": {
                "team_id": team_id,
                "jersey": jersey,
            },
        })
    return player_game_stats_items, player_entities


def _parse_clock_to_seconds(display_value):
    """Converts ESPN's "MM:SS" time-of-possession format (e.g. "23:45")
    into total seconds. Falls back to the raw value unparsed if it's not
    clock-shaped."""
    if not isinstance(display_value, str) or ":" not in display_value:
        return display_value
    minutes, _, seconds = display_value.partition(":")
    try:
        return int(minutes) * 60 + int(seconds)
    except ValueError:
        return display_value


def boxscore_to_team_game_stats(
    summary: dict,
    sport: str,
    compound_key_splits: dict[str, tuple[str, str]],
) -> list[dict]:
    """Returns one team_game_stats item per team from a single game's box
    score -- ESPN's boxscore.teams section (turnovers, total yards, time
    of possession, third/fourth-down and red-zone efficiency, etc.).

    ESPN's team statistics are a flat list with no category grouping, so
    every stat name snake-cases to a unique field directly, unlike
    boxscore_to_player_game_stats. compound_key_splits follows the same
    "one string packs two numbers" contract as the player-stats version,
    with team-level field names (e.g. "thirdDownEff": "3-9" -> conversions,
    attempts) -- a separate map from the player one, since the raw ESPN
    key strings differ at this level ("completionAttempts", not
    "completions/passingAttempts").

    ESPN's team statistics list contains "interceptions" twice with an
    identical value both times; the second occurrence just overwrites the
    first with the same number.
    """
    header = summary["header"]
    event_id = header["id"]
    event_date = None
    for competition in header.get("competitions", []):
        if competition.get("date"):
            event_date = competition["date"][:10]
            break

    items = []
    for team_block in summary.get("boxscore", {}).get("teams", []):
        team_id = str(team_block["team"]["id"])
        line: dict = {}
        for stat in team_block.get("statistics", []):
            name = stat.get("name", "")
            display_value = stat.get("displayValue")
            if name in compound_key_splits:
                first_name, second_name = compound_key_splits[name]
                sep = "/" if "/" in display_value else "-"
                parts = display_value.split(sep, 1)
                if len(parts) == 2:
                    line[first_name] = parse_number(parts[0])
                    line[second_name] = parse_number(parts[1])
                    continue
            if name == "possessionTime":
                line["possession_time_seconds"] = _parse_clock_to_seconds(display_value)
                continue
            line[snake_case(name)] = parse_number(display_value)

        items.append({
            "event_key": event_key(sport, event_id),
            "team_key": team_key(team_id),
            "team_id": team_id,
            "event_date": event_date,
            "stat_line": line,
        })
    return items