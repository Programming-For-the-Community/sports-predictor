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
# "yards"), so each targeted abbreviation is mapped explicitly. Types not
# listed here (rushing/receiving's CAR/REC/LONG etc., not currently a
# training target) fall back to a plain lowercase in _stat_field_name
# below instead.
_STAT_TYPE_NAMES = {
    "YDS": "yards",
    "TD": "touchdowns",
    "SACKS": "sacks",
    "INT": "interceptions",
    "C/ATT": "completions_attempts",
    "QB HUR": "qb_hurries",
}


def _stat_field_name(category: str, type_name: str) -> str:
    category_snake = snake_case(category)
    type_snake = _STAT_TYPE_NAMES.get(type_name) or type_name.strip().lower().replace(" ", "_").replace("-", "_").replace("/", "_")
    return f"{category_snake}_{type_snake}"


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

    Each athlete's stat values are single scalars ("stat": "250"), unlike
    ESPN's compound "24/31"-style strings -- no compound_key_splits
    equivalent is needed here.

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
            for type_entry in category.get("types", []):
                field_name = _stat_field_name(category_name, type_entry.get("name", ""))
                for athlete in type_entry.get("athletes", []):
                    athlete_id = athlete.get("id")
                    if not athlete_id:
                        continue
                    athlete_id = str(athlete_id)
                    stat_lines.setdefault(athlete_id, {})[field_name] = parse_number(athlete.get("stat"))
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
                first_name, second_name = _TEAM_STAT_COMPOUND_SPLITS[category]
                sep = "/" if "/" in str(value) else "-"
                parts = str(value).split(sep, 1)
                if len(parts) == 2:
                    line[first_name] = parse_number(parts[0])
                    line[second_name] = parse_number(parts[1])
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
