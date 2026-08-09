"""
ESPN-response-to-project-schema normalizers shared across every sport that
uses the ESPN site API. Each function accepts the raw ESPN dict plus the
caller's sport string so the same logic works for NFL, NBA, NCAA, etc.

Sport-specific behaviour -- such as which stat keys pack two numbers into
one string -- is passed in by the caller via compound_key_splits rather
than hardcoded here.
"""
from library.parsing import parse_clock_to_seconds, parse_number, snake_case
from library.schema.keys import entity_key, entity_team_key, event_key, player_key, team_key


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
    item = {
        "event_key": event_key(sport, event_id),
        "event_id": event_id,
        "sport": sport,
        "event_type": "head_to_head",
        "event_date": event["date"][:10],
        # Full ISO 8601 timestamp, unlike event_date above -- kickoff time
        # of day is a feature input (see library.features.nfl) and lets
        # the frontend sort/group by actual kickoff, not just calendar day.
        "kickoff_time": event["date"],
        "status": _event_status(event.get("status", {})),
        "participants": participants,
        "season": event["season"]["year"],
        "season_type": event["season"]["type"],
        "week": event.get("week", {}).get("number"),
        "venue_indoor": venue.get("indoor"),
        "venue_name": venue.get("fullName"),
        "venue_city": venue_address.get("city"),
        "venue_state": venue_address.get("state"),
        "weather_temperature": weather.get("temperature"),
    }

    # Coach/injuries/depth-chart are absent on any event not enriched by
    # ingest's _enrich_events (aws-lambdas/nfl/ingest/handler.py), or where
    # that fetch failed. Omitted rather than written as None/empty, same
    # sparse-optional-field convention weather_temperature uses (frequently
    # null for outdoor games ESPN simply didn't report on). Coach is
    # flattened into separate top-level
    # attributes (not a nested map) to match every other feature-ready
    # field on this item; injuries/depth-chart stay as their own
    # list/dict since they're not single scalar values.
    #
    # Distinguishes "no data" (omit) from "fetched, genuinely empty"
    # (keep) via `is not None` rather than a truthiness check for
    # injuries/depth-chart specifically -- an empty injuries list is real
    # signal ("checked, nobody's hurt"), not the same as "never checked".
    if home_coach := event.get("home_coach"):
        item["home_coach_id"] = home_coach.get("coach_id")
        item["home_coach_name"] = home_coach.get("coach_name")
        item["home_coach_experience"] = home_coach.get("experience")
        item["home_coach_season_win_pct"] = home_coach.get("season_win_pct")
        item["home_coach_career_playoff_win_pct"] = home_coach.get("career_playoff_win_pct")
    if away_coach := event.get("away_coach"):
        item["away_coach_id"] = away_coach.get("coach_id")
        item["away_coach_name"] = away_coach.get("coach_name")
        item["away_coach_experience"] = away_coach.get("experience")
        item["away_coach_season_win_pct"] = away_coach.get("season_win_pct")
        item["away_coach_career_playoff_win_pct"] = away_coach.get("career_playoff_win_pct")
    if (home_injuries := event.get("home_injuries")) is not None:
        item["home_injuries"] = home_injuries
    if (away_injuries := event.get("away_injuries")) is not None:
        item["away_injuries"] = away_injuries
    if (home_depth_chart := event.get("home_depth_chart")) is not None:
        item["home_depth_chart"] = home_depth_chart
    if (away_depth_chart := event.get("away_depth_chart")) is not None:
        item["away_depth_chart"] = away_depth_chart

    return item


def roster_to_player_entities(roster: dict, sport: str) -> list[dict]:
    """Every player entity item for one team's current roster (see
    NFLClient.get_roster) -- same item shape boxscore_to_player_game_stats
    already produces for its own player_entities return value, written
    through the same guarded PipelineStorage.upsert_player_entity, so this
    is a second SOURCE for that one write path, not a second write path.

    Unlike a box score, a roster fetch has no game of its own to derive a
    date from -- team_id_as_of comes from the roster payload's own
    "timestamp" field (same [:10] truncation scoreboard_event_to_event_item
    already applies to event_date), not "now" at normalize time, so it
    stays correct even if normalize processes this S3 object some time
    after ingest actually fetched it.

    Includes every position group ESPN returns (offense/defense/
    specialTeam/injuredReserveOrOut/suspended/practiceSquad) -- a player
    on IR or the practice squad is still on this team, not some other
    one, which is exactly the fact this function exists to keep current.

    metadata.position is the athlete's own specific position abbreviation
    ("QB"/"WR"/"CB"/etc, confirmed present on every roster athlete via
    curl) -- distinct from `group`, ESPN's coarse offense/defense/
    specialTeam bucket above. live_features.py's roster-driven candidate
    selection (predict/live_features.py) uses this to know which roster
    players are even eligible for a given slot, since a player with no
    recorded stats yet (a rookie) has no other signal to identify their
    position from.
    """
    team_id = str(roster["team"]["id"])
    as_of_date = roster["timestamp"][:10]
    entities = []
    for group in roster.get("athletes", []):
        for athlete in group.get("items", []):
            entities.append({
                "entity_key": entity_key(sport, athlete["id"]),
                "entity_id": athlete["id"],
                "sport": sport,
                "entity_type": "player",
                "name": athlete.get("displayName", ""),
                # Top-level (not nested in metadata) -- a GSI hash key
                # must be a top-level attribute. See dynamodb-entities.tf's
                # team-index.
                "team_key": entity_team_key(sport, team_id),
                "metadata": {
                    "team_id": team_id,
                    "team_id_as_of": as_of_date,
                    "jersey": athlete.get("jersey"),
                    "position": (athlete.get("position") or {}).get("abbreviation"),
                },
            })
    return entities


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

    Each returned player entity carries metadata.position, same field/shape
    roster_to_player_entities sets from the roster feed -- upsert_player_entity
    (library.storage.pipeline_storage) writes whichever of the two sources
    ran most recently as a full item replacement, not a merge, so this
    entity dropping position would blank out whatever a prior roster sync
    had already set for an actively-playing player, exactly the case
    live_features.py's roster-driven candidate selection depends on it for.
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
                athlete_meta[athlete_id] = (
                    athlete.get("displayName", ""), athlete.get("jersey"),
                    (athlete.get("position") or {}).get("abbreviation"),
                )
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
        display_name, jersey, position = athlete_meta[athlete_id]
        team_id = athlete_team[athlete_id]
        player_game_stats_items.append({
            "event_key": event_key(sport, event_id),
            "player_key": player_key(sport, athlete_id),
            "entity_id": athlete_id,
            "team_id": team_id,
            "event_date": event_date,
            "sport": sport,
            "stat_line": line,
        })
        player_entities.append({
            "entity_key": entity_key(sport, athlete_id),
            "entity_id": athlete_id,
            "sport": sport,
            "entity_type": "player",
            "name": display_name,
            # Top-level (not nested in metadata) -- a GSI hash key must be
            # a top-level attribute. See dynamodb-entities.tf's team-index.
            "team_key": entity_team_key(sport, team_id),
            "metadata": {
                "team_id": team_id,
                # This game's own event_date -- lets upsert_player_entity
                # guard against an out-of-order write (a concurrent backfill
                # processing games out of chronological order) clobbering a
                # player's team_id with a stale one. See that method's own
                # docstring.
                "team_id_as_of": event_date,
                "jersey": jersey,
                "position": position,
            },
        })
    return player_game_stats_items, player_entities


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
                line["possession_time_seconds"] = parse_clock_to_seconds(display_value)
                continue
            line[snake_case(name)] = parse_number(display_value)

        items.append({
            "event_key": event_key(sport, event_id),
            "team_key": team_key(team_id),
            "team_id": team_id,
            "event_date": event_date,
            "sport": sport,
            "stat_line": line,
        })
    return items