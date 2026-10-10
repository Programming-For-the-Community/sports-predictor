"""
NHL-specific normalization on top of library.normalize.espn's shared
ESPN normalizers. Hockey differs from the other ESPN head-to-head sports
in three ways this module owns:

- A game can end in overtime or a shootout. ESPN's final score credits
  the shootout winner with one extra goal, so each completed event also
  carries per-period scores, a regulation score, and
  went_to_overtime/decided_by_shootout flags.
- The scoreboard names each team's probable starting goalie.
- The box score splits players into skater groups (forwards/defenses)
  and goalies with different stat sets, and reports ice time as "MM:SS".

Every stat value written is numeric or None -- never a raw string.
"""
from library.features import nhl_teams
from library.normalize import espn
from library.parsing import parse_clock_to_seconds, parse_number, snake_case
from library.schema.keys import team_key

PRESEASON_TYPE = 1  # ESPN season.type: 1=preseason, 2=regular, 3=postseason
REGULATION_PERIODS = 3

# ESPN's NHL roster injury-status vocabulary.
CURRENT_INJURY_STATUSES = frozenset({"Day-To-Day", "Out", "Injured Reserve", "Suspension"})

POSITION_GROUP_SKATER = "skater"
POSITION_GROUP_GOALIE = "goalie"

_GOALIE_CATEGORY = "goalies"
_CLOCK_KEYS = {"timeOnIce", "powerPlayTimeOnIce", "shortHandedTimeOnIce", "evenStrengthTimeOnIce"}
_SHOOTOUT_DETAIL_MARKER = "/SO"

_PROBABLE_STARTING_GOALIE = "probableStartingGoalie"


def _numeric_or_none(value):
    number = parse_number(value)
    if isinstance(number, bool) or not isinstance(number, (int, float)):
        return None
    return number


def _clock_seconds_or_none(value) -> int | None:
    seconds = parse_clock_to_seconds(value)
    return seconds if isinstance(seconds, int) and not isinstance(seconds, bool) else None


def _stat_field(key: str, value) -> tuple[str, int | float | None]:
    if key in _CLOCK_KEYS:
        return f"{snake_case(key)}_seconds", _clock_seconds_or_none(value)
    return snake_case(key), _numeric_or_none(value)


def _competitor_team_ids(competition: dict) -> list[str | None]:
    return [str(c.get("team", {}).get("id")) if c.get("team", {}).get("id") is not None else None
            for c in competition.get("competitors", [])]


def is_ingestable_event(event: dict) -> bool:
    """False for preseason, and for an All-Star or international-tournament
    game (ESPN lists those under the regular season with non-franchise
    teams)."""
    if event.get("season", {}).get("type") == PRESEASON_TYPE:
        return False
    competitions = event.get("competitions") or [{}]
    team_ids = _competitor_team_ids(competitions[0])
    return bool(team_ids) and all(nhl_teams.is_franchise_team(team_id) for team_id in team_ids)


def is_ingestable_summary(summary: dict) -> bool:
    """is_ingestable_event's franchise check, for a box-score payload."""
    competitions = summary.get("header", {}).get("competitions") or [{}]
    team_ids = _competitor_team_ids(competitions[0])
    return bool(team_ids) and all(nhl_teams.is_franchise_team(team_id) for team_id in team_ids)


def _period_scores(competitor: dict) -> list[int]:
    scores = []
    for line in competitor.get("linescores") or []:
        value = _numeric_or_none(line.get("value"))
        scores.append(int(value) if value is not None else 0)
    return scores


def _probable_goalie(competitor: dict) -> tuple[str | None, str | None]:
    """(athlete id, ESPN status name -- "Confirmed" or "Expected")."""
    for probable in competitor.get("probables") or []:
        if probable.get("name") != _PROBABLE_STARTING_GOALIE:
            continue
        athlete_id = (probable.get("athlete") or {}).get("id") or probable.get("playerId")
        if athlete_id is None:
            continue
        return str(athlete_id), (probable.get("status") or {}).get("name")
    return None, None


def scoreboard_event_to_event_item(event: dict, sport: str) -> dict:
    """espn.scoreboard_event_to_event_item plus the hockey fields:
    competition_type, each side's probable goalie, and -- once completed --
    period_scores/regulation_score per participant and the
    went_to_overtime/decided_by_shootout flags.

    result.score stays ESPN's own final score, which includes the
    shootout winner's extra goal."""
    item = espn.scoreboard_event_to_event_item(event, sport)
    competition = event["competitions"][0]
    item["competition_type"] = (competition.get("type") or {}).get("abbreviation")

    competitors_by_role = {c.get("homeAway"): c for c in competition["competitors"]}
    for role in ("home", "away"):
        goalie_id, goalie_status = _probable_goalie(competitors_by_role.get(role) or {})
        if goalie_id is not None:
            item[f"{role}_probable_goalie_id"] = goalie_id
            item[f"{role}_probable_goalie_status"] = goalie_status

    if item["status"] != "completed":
        return item

    periods_played = 0
    for participant in item["participants"]:
        period_scores = _period_scores(competitors_by_role.get(participant["role"]) or {})
        if not period_scores:
            continue
        periods_played = max(periods_played, len(period_scores))
        participant["result"]["period_scores"] = period_scores
        participant["result"]["regulation_score"] = sum(period_scores[:REGULATION_PERIODS])

    if periods_played:
        status_detail = event.get("status", {}).get("type", {}).get("detail") or ""
        item["went_to_overtime"] = periods_played > REGULATION_PERIODS
        item["decided_by_shootout"] = _SHOOTOUT_DETAIL_MARKER in status_detail
    return item


def _athlete_stat_line(keys: list[str], stats: list) -> dict:
    line = dict(_stat_field(key, value) for key, value in zip(keys, stats))
    goals, assists = line.get("goals"), line.get("assists")
    if goals is not None and assists is not None:
        line["points"] = goals + assists
    return line


def _team_categories(summary: dict) -> list[tuple[str, dict]]:
    """(team id, box-score stat category) for both teams, in ESPN order."""
    return [
        (str(team_block["team"]["id"]), category)
        for team_block in summary.get("boxscore", {}).get("players", [])
        for category in team_block.get("statistics", [])
    ]


def _athletes_who_played(category: dict) -> list[tuple[dict, list]]:
    """(athlete, stats) for a box-score category's entries, in ESPN's
    order, without scratches and DNP stubs."""
    played = []
    for entry in category.get("athletes", []):
        athlete = entry.get("athlete") or {}
        stats = entry.get("stats") or []
        if athlete.get("id") is not None and not athlete.get("scratched") and stats:
            played.append((athlete, stats))
    return played


def boxscore_to_player_game_stats(summary: dict, sport: str) -> tuple[list[dict], list[dict]]:
    """(player_game_stats items, player entity items) for one game. Each
    stats item also carries position (ESPN's abbreviation) and
    position_group ("skater"/"goalie"), and each goalie's carries started -- the first goalie ESPN lists for a team is
    the one who started. Scratched players and DNP stubs are dropped.

    Stat keys are snake-cased with no category prefix (a player is only
    ever a skater or a goalie); ice-time fields become *_seconds, and
    skaters gain points (goals + assists)."""
    header = summary["header"]
    event_id = header["id"]
    event_date = espn._event_date_from_header(header)

    stat_lines: dict[str, dict] = {}
    athlete_meta: dict[str, tuple] = {}
    athlete_team: dict[str, str] = {}
    position_groups: dict[str, str] = {}
    started: dict[str, bool] = {}

    for team_id, category in _team_categories(summary):
        is_goalie_category = category.get("name") == _GOALIE_CATEGORY
        keys = category.get("keys", [])
        for index, (athlete, stats) in enumerate(_athletes_who_played(category)):
            athlete_id = athlete["id"]
            stat_lines[athlete_id] = _athlete_stat_line(keys, stats)
            athlete_team[athlete_id] = team_id
            athlete_meta[athlete_id] = (
                athlete.get("displayName", ""), athlete.get("jersey"),
                (athlete.get("position") or {}).get("abbreviation"),
            )
            position_groups[athlete_id] = POSITION_GROUP_GOALIE if is_goalie_category else POSITION_GROUP_SKATER
            if is_goalie_category:
                started[athlete_id] = index == 0

    stats_items, player_entities = espn._build_player_items(
        sport, event_id, event_date, stat_lines, athlete_meta, athlete_team,
    )
    for item in stats_items:
        athlete_id = item["entity_id"]
        item["position_group"] = position_groups[athlete_id]
        item["position"] = athlete_meta[athlete_id][2]
        if athlete_id in started:
            item["started"] = started[athlete_id]
    return stats_items, player_entities


def _team_totals_from_players(stats_items: list[dict]) -> dict[str, dict]:
    """{team_key: {shots_missed, saves, shots_against}} -- team totals
    ESPN's team box score doesn't carry itself."""
    totals: dict[str, dict] = {}
    for item in stats_items:
        line = item["stat_line"]
        team_totals = totals.setdefault(team_key(item["team_id"]), {"shots_missed": 0, "saves": 0, "shots_against": 0})
        fields = ("saves", "shots_against") if item["position_group"] == POSITION_GROUP_GOALIE else ("shots_missed",)
        for field in fields:
            team_totals[field] += line.get(field) or 0
    return totals


def boxscore_to_team_game_stats(summary: dict, sport: str) -> list[dict]:
    """One team_game_stats item per team: ESPN's own team box score, plus
    shots_missed/saves/shots_against summed from that team's players."""
    items = espn.boxscore_to_team_game_stats(summary, sport, {})
    stats_items, _ = boxscore_to_player_game_stats(summary, sport)
    totals = _team_totals_from_players(stats_items)
    for item in items:
        item["stat_line"] = {name: _numeric_or_none(value) for name, value in item["stat_line"].items()}
        item["stat_line"].update(totals.get(item["team_key"], {}))
    return items
