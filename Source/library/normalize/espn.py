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
        "entity_key": entity_key(sport, team_id, "team"),
        "entity_id": team_id,
        "sport": sport,
        "entity_type": "team",
        "name": team.get("displayName", team_id),
        "metadata": {
            "abbreviation": team.get("abbreviation"),
            "location": team.get("location"),
            "nickname": team.get("nickname") or team.get("name"),
            # Bare 6-digit hex, no "#" (confirmed live, e.g. "c8102e") --
            # frontend's own job to prefix it. NFL prefers its own
            # hand-typed static/nfl_team_colors.dart table over this (real
            # brand colors, longer-established) -- this exists for every
            # other sport, which has no such table (see teamDisplayFor's
            # own doc comment).
            "color": team.get("color"),
        },
    }


# ESPN status.type.name values for a game that will never be played (or
# resume) under this event_id -- status.type.completed is False for all
# of these, same as a genuinely upcoming game, so without this a canceled/
# postponed game defaults to "scheduled" and sits there permanently (see
# [[project-nfl-data-quality-edge-cases]] for the 3 known real records
# this was confirmed against: a canceled Pro Bowl, the Damar Hamlin
# permanently-suspended game, and a hurricane-postponed game replayed
# under a different event_id). STATUS_SUSPENDED/STATUS_FORFEIT are
# included on the same reasoning even though no known record has hit
# them yet -- ESPN's public API is unofficial, so any status wholly
# distinct from "will be/was played normally" gets the same treatment.
_NON_PLAYED_STATUS_NAMES = {"STATUS_CANCELED", "STATUS_POSTPONED", "STATUS_SUSPENDED", "STATUS_FORFEIT"}


def _event_status(status: dict) -> str:
    status_type = status.get("type", {})
    if status_type.get("completed"):
        return "completed"
    if status_type.get("name") in _NON_PLAYED_STATUS_NAMES:
        return "canceled"
    return "scheduled"


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

    # Sport-agnostic passthrough of ESPN's own event-level "notes" --
    # confirmed live, 2026-08-16, that NBA in-season-tournament (NBA Cup)
    # group-play games carry event["notes"] == [{"type": "event",
    # "headline": "NBA Cup - Group Play"}], a sibling of "competitions",
    # not nested inside it. Stored as the raw headline string rather than
    # a parsed boolean so interpretation stays with whichever sport's own
    # feature/serving code cares about it (only NBA's season_projection.py
    # does today) -- this function itself doesn't know what "NBA Cup"
    # means, same "generic extraction, sport-specific interpretation"
    # split every other optional field on this item already follows. Omit
    # rather than write None when absent, same convention as the
    # coach/injuries fields below.
    notes = event.get("notes") or []
    if tournament_headline := next((n.get("headline") for n in notes if n.get("headline")), None):
        item["tournament_note"] = tournament_headline

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


def _flatten_roster_athletes(raw_athletes: list) -> list[dict]:
    """NFL's roster groups athletes by position group (offense/defense/
    specialTeam/injuredReserveOrOut/suspended/practiceSquad --
    `[{"items": [athlete, ...]}, ...]`); NBA's is a flat list of athlete
    dicts with no grouping wrapper at all -- confirmed live, 2026-08-14
    (see project-nba-onboarding memory). Detected per-entry rather than
    branched on sport, since that's the actual structural signal and keeps
    this function sport-agnostic, matching this module's own docstring."""
    flat = []
    for entry in raw_athletes:
        if "items" in entry:
            flat.extend(entry["items"])
        else:
            flat.append(entry)
    return flat


def roster_to_player_entities(roster: dict, sport: str) -> list[dict]:
    """Every player entity item for one team's current roster (see
    NFLClient.get_roster/NBAClient.get_roster) -- same item shape
    boxscore_to_player_game_stats already produces for its own
    player_entities return value, written through the same guarded
    PipelineStorage.upsert_player_entity, so this is a second SOURCE for
    that one write path, not a second write path.

    Unlike a box score, a roster fetch has no game of its own to derive a
    date from -- team_id_as_of comes from the roster payload's own
    "timestamp" field (same [:10] truncation scoreboard_event_to_event_item
    already applies to event_date), not "now" at normalize time, so it
    stays correct even if normalize processes this S3 object some time
    after ingest actually fetched it.

    Includes every player ESPN's roster response returns for the team,
    including IR/practice-squad-equivalent statuses where the sport has
    them (NFL's own position groups) -- a player on IR is still on this
    team, not some other one, which is exactly the fact this function
    exists to keep current.

    metadata.position is the athlete's own specific position abbreviation
    ("QB"/"WR"/"CB" for NFL, "F"/"G"/"C" for NBA -- same
    position.abbreviation shape confirmed on both, live). live_features.py's
    roster-driven candidate selection (predict/live_features.py) uses this
    to know which roster players are even eligible for a given slot, since
    a player with no recorded stats yet (a rookie) has no other signal to
    identify their position from.
    """
    team_id = str(roster["team"]["id"])
    as_of_date = roster["timestamp"][:10]
    entities = []
    for athlete in _flatten_roster_athletes(roster.get("athletes", [])):
        entities.append({
            "entity_key": entity_key(sport, athlete["id"], "player"),
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


# Same status vocabulary EspnCoreApiClient.get_team_injuries filters to
# (NFL's separate core-API injury-report endpoint) -- ESPN uses this same
# three-status set project-wide wherever it reports a current injury
# report, so it's a reasonable default here too. Duplicated locally
# rather than imported from library.http.espn_core: that module is a
# different concern (an HTTP client for a different ESPN host), and
# library.features.common already holds the canonical status-severity
# vocabulary (_INJURY_STATUS_ORDINAL/_TEAM_INJURY_COUNT_STATUSES) that
# this module deliberately doesn't import either, to keep normalize free
# of feature-layer knowledge.
_CURRENT_INJURY_STATUSES = {"Questionable", "Doubtful", "Out"}


def roster_to_team_injuries(roster: dict) -> list[dict]:
    """Extracts each currently-injured athlete's status from a roster
    response. NBA's site-API roster embeds `injuries` directly on each
    athlete (confirmed live, 2026-08-14 -- see NBAClient.get_roster's own
    docstring) -- unlike NFL, which needs a separate core-API injury-report
    call (EspnCoreApiClient.get_team_injuries), so no extra fetch is
    needed for what's already collected here. Returns the same
    [{"entity_id", "status"}, ...] contract that function returns (raw
    ESPN status string unmapped -- severity thresholding is a
    feature-layer concern, library.features.common), so downstream code
    doesn't need to know which of the two sources an event's injuries
    field came from.

    UNVERIFIED: only the existence of athlete["injuries"] was confirmed
    live, not the exact key holding each entry's status string -- this
    assumes a top-level "status" string per entry (matching
    EspnCoreApiClient.get_team_injuries' own confirmed shape from the
    same ESPN status vocabulary), with a fallback to a nested
    type.description shape in case the site API differs from the core
    API here. Treat as needing a real payload check before trusting it in
    production (see project-nba-onboarding memory).
    """
    result = []
    for athlete in _flatten_roster_athletes(roster.get("athletes", [])):
        athlete_id = athlete.get("id")
        if athlete_id is None:
            continue
        for injury in athlete.get("injuries") or []:
            status = injury.get("status") or (injury.get("type") or {}).get("description")
            if status not in _CURRENT_INJURY_STATUSES:
                continue
            result.append({"entity_id": athlete_id, "status": status})
    return result


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
            # NFL's categories are always named ("passing"/"rushing"/etc,
            # prefixed below); NBA's single stat block has no "name" field
            # at all (only a display-label "names" array) -- confirmed
            # live, 2026-08-14. Falling back to a fabricated "misc" prefix
            # there would silently rename "points" to "misc_points",
            # breaking TARGET_STAT's exact stat_line key match (see
            # model-training/nba/train_player_prop_model.py). Distinguish
            # "genuinely unnamed" (category.get("name") is None -- no
            # prefix at all) from "named" (prefix as before) rather than
            # defaulting a name in.
            raw_category_name = category.get("name")
            category_name = snake_case(raw_category_name) if raw_category_name is not None else None
            keys = category.get("keys", [])
            for athlete_entry in category.get("athletes", []):
                athlete = athlete_entry["athlete"]
                # Confirmed live, 2025-12-07 game 401810216 (2025-26 season):
                # a deep-bench player held out for "COACH'S DECISION" can
                # carry a stub athlete object with no "id" at all (just
                # `links`/`shortName`) -- every other athlete entry in the
                # same response has a normal numeric id. No stat line is
                # recoverable without an id to key it by, and a scoreless
                # DNP entry has nothing worth capturing anyway.
                athlete_id = athlete.get("id")
                if athlete_id is None:
                    continue
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
                    if category_name is None or snake_key == category_name or snake_key.startswith(f"{category_name}_"):
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
            "entity_key": entity_key(sport, athlete_id, "player"),
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