"""
CFBD-response-to-project-schema normalizers -- the NCAAFB equivalent of
library/normalize/espn.py. Kept as its own module (not folded into
espn.py) since CFBD's response shapes share almost nothing structurally
with ESPN's, despite both eventually producing the same project schema.

Each function is a pure transform (no S3/DynamoDB/HTTP calls of its own),
same convention as espn.py -- the aws-lambdas/ncaafb/{ingest,normalize}
Lambdas own all the I/O and enrichment, this module only ever maps one
already-fetched dict/list into project schema items.
"""
from library.parsing import parse_clock_to_seconds, parse_number, snake_case
from library.schema.keys import entity_key, entity_team_key, event_key, player_key, team_key

# CFBD's box-score stat "types" are terse, already-uppercase abbreviations
# (confirmed live against real 2025 data: passing types AVG/C/ATT/INT/
# QBR/TD/YDS, defensive types PD/QB HUR/SACKS/SOLO/TD/TFL/TOT) -- snake_case
# (built for camelCase JSON keys) would shred an abbreviation like "CAR"
# into "c_a_r" one letter at a time, and even a clean lowercase wouldn't
# produce the TARGET_STAT names Terraform/dynamodb-sport-registry.tf's
# ncaafb_player_prop_stats already commits to (e.g. "YDS" -> "yds", not
# "yards"), so each targeted abbreviation is mapped explicitly. CAR/REC map
# to "attempts"/"receptions" (not a literal transliteration) so the
# resulting field names (rushing_attempts, receiving_receptions) match
# library/features/ncaafb.py's leader-identification volume stats -- the
# same names library/features/nfl.py's identify_lead_rusher/
# identify_lead_receiver already use for ESPN's own equivalent fields.
# Types not listed here fall back to a plain lowercase in _stat_field_name
# below instead. "C/ATT" is deliberately absent -- it's a compound value,
# handled by _PLAYER_STAT_COMPOUND_SPLITS below before this map is ever
# consulted.
_STAT_TYPE_NAMES = {
    "YDS": "yards",
    "TD": "touchdowns",
    "SACKS": "sacks",
    "INT": "interceptions",
    "CAR": "attempts",
    "REC": "receptions",
    "QB HUR": "qb_hurries",
}

# CFBD packs some player-level stats as one slash-separated string, same
# compound-value pattern as its team-level stats (_TEAM_STAT_COMPOUND_SPLITS
# below) -- confirmed live against real 2025 data ("C/ATT": "24/38", "FG":
# "1/2", "XP": "3/3"). Keyed by the raw type name, checked before
# _stat_field_name/_STAT_TYPE_NAMES ever see it -- a real gap in the
# original implementation, which stored these as unparsed strings
# (rolling_player_stat_averages silently skips non-numeric stat_line
# values, so passing attempts/completions and kicking makes/attempts were
# being dropped entirely, not just mis-typed).
_PLAYER_STAT_COMPOUND_SPLITS: dict[str, tuple[str, str]] = {
    "C/ATT": ("completions", "attempts"),
    "FG": ("field_goals_made", "field_goals_attempted"),
    "XP": ("extra_points_made", "extra_points_attempted"),
}


def _stat_field_name(category: str, type_name: str) -> str:
    category_snake = snake_case(category)
    type_snake = _STAT_TYPE_NAMES.get(type_name) or type_name.strip().lower().replace(" ", "_").replace("-", "_").replace("/", "_")
    return f"{category_snake}_{type_snake}"


def _split_compound_value(value) -> tuple | None:
    text = str(value)
    sep = "/" if "/" in text else "-" if "-" in text else None
    if sep is None:
        return None
    parts = text.split(sep, 1)
    return (parse_number(parts[0]), parse_number(parts[1])) if len(parts) == 2 else None


def _resolve_team_id(box_score: dict, home_away: str | None) -> str | None:
    """home_id/away_id for a box-score team_block's own homeAway value --
    None for anything other than exactly "home" or "away" (e.g. a
    malformed or neutral-site value CFBD doesn't actually use today),
    rather than defaulting to away_id the way a bare ternary would."""
    if home_away == "home":
        return box_score.get("home_id")
    if home_away == "away":
        return box_score.get("away_id")
    return None


def _team_won(game: dict, home: bool) -> bool | None:
    home_points, away_points = game.get("homePoints"), game.get("awayPoints")
    if home_points is None or away_points is None:
        return None
    return (home_points > away_points) if home else (away_points > home_points)


def team_to_entity(team: dict, sport: str) -> dict:
    """One CFBD /teams entry -> one team entity item -- the NCAAFB
    equivalent of espn.py's team_to_entity. Only ever called from
    data-backfills/ncaafb/backfill.py's seed_teams, using the most recent
    backfilled season's /teams response, not once per season -- CFBD's
    /teams is season-scoped (conference realignment), and a team entity
    row isn't a dated snapshot, so seeding from every season would let
    whichever season's fetch happens to write last win the race for a
    given team's conference value (see backfill.py's own docstring)."""
    team_id = str(team["id"])
    location = team.get("location") or {}
    return {
        "entity_key": entity_key(sport, team_id),
        "entity_id": team_id,
        "sport": sport,
        "entity_type": "team",
        "name": team.get("school", team_id),
        "metadata": {
            "abbreviation": team.get("abbreviation"),
            "mascot": team.get("mascot"),
            "conference": team.get("conference"),
            "venue_indoor": location.get("dome"),
            # Home-stadium coordinates -- confirmed live that CFBD's own
            # /teams response carries these directly on `location`, unlike
            # NFL/ESPN which has no coordinate field at all (library/
            # features/nfl_teams.py hand-maintains a static TEAM_COORDINATES
            # table instead). library/features/ncaafb.py's build_event_
            # features reads these back out per team via FeatureStorage.
            # get_entity to compute travel distance, rather than a second
            # hardcoded table that would drift as FBS realignment happens.
            "latitude": location.get("latitude"),
            "longitude": location.get("longitude"),
        },
    }


def game_to_event_item(game: dict, sport: str) -> dict:
    """One CFBD /games entry -> one event item -- the NCAAFB equivalent
    of scoreboard_event_to_event_item. home_id/away_id come straight from
    CFBD's own homeId/awayId (confirmed live, Phase 1), unlike ESPN's
    nested competitions[0].competitors[] shape.

    score/won are left as None for a game that hasn't been played yet
    (CFBD's homePoints/awayPoints are genuinely absent pre-game), rather
    than ESPN's own "0" placeholder -- same "missing, not fabricated"
    discipline this project already applies to injuries.

    home_conference/away_conference/conference_game are CFBD's own
    per-game values, passed through as-is -- library/features/ncaafb.py
    (a later phase) derives the actual is_conference_game feature from
    these, falling back to a season-scoped /teams lookup for games CFBD
    hasn't computed conference_game for yet (see project plan).

    is_playoff_game is derived (not passed through) from CFBD's own
    `playoff` field -- confirmed live it's a populated object
    ({"competition": "cfp", "round": ...}) for real 12-team CFP games and
    None for every other game, including ordinary bowls -- a cleaner,
    directly-verified signal than string-matching the `notes` bowl-name
    field the project plan originally proposed. Always set (never
    sparse), unlike the coach/rank fields below -- it needs no enrichment
    step, only this function's own raw `game` argument.
    library/features/ncaafb.py derives is_bowl_game from this plus
    season_type ("postseason" and NOT a playoff game).

    home_coach/away_coach/home_current_rank/away_current_rank/
    venue_indoor are only present when aws-lambdas/ncaafb/ingest/
    enrichment.py successfully attached them before this game was
    written to S3 -- omitted here (not written as None) when absent,
    same convention scoreboard_event_to_event_item already uses for
    NFL's coach/injury/depth-chart fields.
    """
    event_id = str(game["id"])
    home_id, away_id = str(game["homeId"]), str(game["awayId"])
    participants = [
        {
            "entity_id": home_id,
            "role": "home",
            "result": {"score": game.get("homePoints"), "won": _team_won(game, home=True)},
        },
        {
            "entity_id": away_id,
            "role": "away",
            "result": {"score": game.get("awayPoints"), "won": _team_won(game, home=False)},
        },
    ]
    item = {
        "event_key": event_key(sport, event_id),
        "event_id": event_id,
        "sport": sport,
        "event_type": "head_to_head",
        "event_date": game["startDate"][:10],
        "kickoff_time": game["startDate"],
        "status": "completed" if game.get("completed") else "scheduled",
        "participants": participants,
        "season": game["season"],
        "season_type": game["seasonType"],
        "week": game.get("week"),
        "venue_indoor": game.get("venue_indoor"),
        "venue_name": game.get("venue"),
        "home_conference": game.get("homeConference"),
        "away_conference": game.get("awayConference"),
        "conference_game": game.get("conferenceGame"),
        "is_playoff_game": game.get("playoff") is not None,
    }

    if home_coach := game.get("home_coach"):
        item["home_coach_name"] = home_coach.get("coach_name")
        item["home_coach_experience"] = home_coach.get("coach_experience")
        item["home_coach_season_win_pct"] = home_coach.get("season_win_pct")
    if away_coach := game.get("away_coach"):
        item["away_coach_name"] = away_coach.get("coach_name")
        item["away_coach_experience"] = away_coach.get("coach_experience")
        item["away_coach_season_win_pct"] = away_coach.get("season_win_pct")
    if (home_rank := game.get("home_current_rank")) is not None:
        item["home_current_rank"] = home_rank
    if (away_rank := game.get("away_current_rank")) is not None:
        item["away_current_rank"] = away_rank

    return item


def game_player_stats_to_player_game_stats(game_box_score: dict, sport: str) -> tuple[list[dict], list[dict]]:
    """Returns (player_game_stats items, player entity items) for one
    game's CFBD /games/players entry -- the NCAAFB equivalent of
    boxscore_to_player_game_stats. Requires home_id/away_id/event_date
    already injected onto game_box_score by aws-lambdas/ncaafb/ingest/
    handler.py's _annotate_box_scores (see that function's own docstring
    for why the join to /games has to happen at ingest time, not here).

    Most athlete stat values are single scalars ("stat": "250"); three
    (passing C/ATT, kicking FG, kicking XP) are slash-separated compound
    values instead, split via _PLAYER_STAT_COMPOUND_SPLITS the same way
    _TEAM_STAT_COMPOUND_SPLITS below handles CFBD's team-level compounds.

    metadata.position is always None -- CFBD's box-score athletes carry
    no position field (unlike ESPN's); position would come from the
    roster feed instead, which isn't wired into ingest yet (see project
    memory)."""
    event_id = str(game_box_score["id"])
    event_date = game_box_score.get("event_date")

    stat_lines: dict[str, dict] = {}
    athlete_names: dict[str, str] = {}
    athlete_team: dict[str, str] = {}

    for team_block in game_box_score.get("teams", []):
        team_id = _resolve_team_id(game_box_score, team_block.get("homeAway"))
        if team_id is None:
            continue
        for category in team_block.get("categories", []):
            category_name = category.get("name", "misc")
            category_snake = snake_case(category_name)
            for type_entry in category.get("types", []):
                type_name = type_entry.get("name", "")
                compound_names = _PLAYER_STAT_COMPOUND_SPLITS.get(type_name)
                field_name = None if compound_names else _stat_field_name(category_name, type_name)
                for athlete in type_entry.get("athletes", []):
                    athlete_id = athlete.get("id")
                    if not athlete_id:
                        continue
                    athlete_id = str(athlete_id)
                    line = stat_lines.setdefault(athlete_id, {})
                    raw_value = athlete.get("stat")
                    split = _split_compound_value(raw_value) if compound_names else None
                    if split is not None:
                        line[f"{category_snake}_{compound_names[0]}"] = split[0]
                        line[f"{category_snake}_{compound_names[1]}"] = split[1]
                    else:
                        line[field_name or _stat_field_name(category_name, type_name)] = parse_number(raw_value)
                    athlete_names[athlete_id] = athlete.get("name", "")
                    athlete_team[athlete_id] = team_id

    player_game_stats_items = []
    player_entities = []
    for athlete_id, line in stat_lines.items():
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
            "name": athlete_names[athlete_id],
            "team_key": entity_team_key(sport, team_id),
            "metadata": {
                "team_id": team_id,
                "team_id_as_of": event_date,
                "jersey": None,
                "position": None,
            },
        })
    return player_game_stats_items, player_entities


def roster_to_player_entities(roster: list[dict], sport: str, as_of_date: str) -> list[dict]:
    """Every player entity item from one CFBD /roster response (see
    CFBDClient.get_roster) -- the NCAAFB equivalent of espn.py's
    roster_to_player_entities, and the fix for
    game_player_stats_to_player_game_stats' own documented gap
    (metadata.position always None from a box score alone).

    Confirmed live against CFBD's real /roster schema: a flat list of
    {"id", "team", "firstName", "lastName", "position", "jersey", ...}
    objects, one per player, no grouping by team the way ESPN's roster
    nests athletes under position groups. Note there's no "teamId" here --
    only "team", a school name string -- so "teamId" is expected to
    already be injected by ingest/handler.py's own _annotate_roster
    before this ever runs; a roster payload that skipped that step will
    have every player dropped (see the "id"/"teamId" guard below), same
    as one whose school wasn't found in that season's teams cache. "id"
    is assumed to be the SAME id space CFBD's own box scores assign
    (library/normalize/ncaafb.py's game_player_stats_to_player_game_stats
    reads athlete["id"] from /games/players) -- CFBD has one player
    database backing both endpoints, so this should hold, but this half
    is still unconfirmed against real data.

    as_of_date is the ingest fetch's own timestamp (there's no per-payload
    "as of" field on this endpoint the way ESPN's roster has "timestamp"),
    truncated to a date by the caller -- same team_id_as_of role
    espn.py's roster_to_player_entities docstring describes, used by
    PipelineStorage.upsert_player_entity's staleness guard.

    A player missing "id" or "teamId" is skipped -- CFBD's roster is known
    to include walk-ons and practice-squad-equivalent entries some seasons
    that may lack one or the other; nothing useful can be upserted without
    both."""
    entities = []
    for player in roster:
        athlete_id = player.get("id")
        team_id = player.get("teamId")
        if athlete_id is None or team_id is None:
            continue
        athlete_id, team_id = str(athlete_id), str(team_id)
        name = " ".join(part for part in (player.get("firstName"), player.get("lastName")) if part)
        entities.append({
            "entity_key": entity_key(sport, athlete_id),
            "entity_id": athlete_id,
            "sport": sport,
            "entity_type": "player",
            "name": name,
            "team_key": entity_team_key(sport, team_id),
            "metadata": {
                "team_id": team_id,
                "team_id_as_of": as_of_date,
                "jersey": player.get("jersey"),
                "position": player.get("position"),
            },
        })
    return entities


# CFBD's team box score packs some stats as one dash-separated string,
# same pattern as ESPN's own team-stat compound values -- confirmed live
# against real 2025 data. A separate map from _STAT_TYPE_NAMES above since
# these are category names (team-level), not types (player-level).
_TEAM_STAT_COMPOUND_SPLITS: dict[str, tuple[str, str]] = {
    "thirdDownEff": ("third_down_conversions", "third_down_attempts"),
    "fourthDownEff": ("fourth_down_conversions", "fourth_down_attempts"),
    "completionAttempts": ("completions", "pass_attempts"),
    "totalPenaltiesYards": ("penalties", "penalty_yards"),
}


def game_team_stats_to_team_game_stats(game_team_box_score: dict, sport: str) -> list[dict]:
    """Returns one team_game_stats item per team from a single game's
    CFBD /games/teams entry -- the NCAAFB equivalent of
    boxscore_to_team_game_stats. Confirmed live against real 2025 data:
    [{"id": game_id, "teams": [{"teamId", "team": school_name,
    "conference", "homeAway", "points", "stats": [{"category", "stat"}]}]}].

    Unlike get_game_player_stats, each team block DOES carry its own
    numeric teamId directly -- team_id is read from there, not resolved
    via homeAway (falls back to _resolve_team_id, using ingest's injected
    home_id/away_id, only if teamId is ever absent). event_date still has
    to come from ingest's _annotate_box_scores -- confirmed live that this
    endpoint's own game entries carry no date of their own, only "id" and
    "teams"."""
    event_id = str(game_team_box_score["id"])
    event_date = game_team_box_score.get("event_date")

    items = []
    for team_block in game_team_box_score.get("teams", []):
        raw_team_id = team_block.get("teamId")
        team_id = str(raw_team_id) if raw_team_id is not None else _resolve_team_id(game_team_box_score, team_block.get("homeAway"))
        if team_id is None:
            continue

        line: dict = {}
        for stat in team_block.get("stats", []):
            category = stat.get("category", "")
            value = stat.get("stat")
            if category == "possessionTime":
                line["possession_time_seconds"] = parse_clock_to_seconds(value)
                continue
            if category in _TEAM_STAT_COMPOUND_SPLITS:
                split = _split_compound_value(value)
                if split is not None:
                    first_name, second_name = _TEAM_STAT_COMPOUND_SPLITS[category]
                    line[first_name], line[second_name] = split
                    continue
            line[snake_case(category)] = parse_number(value)

        items.append({
            "event_key": event_key(sport, event_id),
            "team_key": team_key(team_id),
            "team_id": team_id,
            "event_date": event_date,
            "sport": sport,
            "stat_line": line,
        })
    return items
