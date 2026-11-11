"""
A team's box-score history with each game's opponent row attached, so a
team's defence ("allowed") reads straight from the same history as its
offence. Training folds rows in one game at a time (paired_row); serving
pairs a team's stored rows against the bulk-loaded team_game_stats it
already holds (paired_rows). Both produce the same row shape:

    {"event_key", "event_date", "team_id", "season", "stat_line",
     "opponent_stat_line", "points_for", "points_against", "periods",
     "advanced"}

`advanced` is whatever the own team_game_stats row carried under that key
(NCAAFB's CFBD advanced game stats), else None.
"""


def _scores(event: dict | None, team_id: str) -> tuple[float | None, float | None]:
    participants = (event or {}).get("participants", [])
    own = next((p for p in participants if p.get("entity_id") == team_id), {})
    opponent = next((p for p in participants if p.get("entity_id") != team_id), {})
    return (own.get("result") or {}).get("score"), (opponent.get("result") or {}).get("score")


def paired_row(event: dict | None, own_row: dict, opponent_row: dict | None) -> dict:
    team_id = own_row["team_id"]
    points_for, points_against = _scores(event, team_id)
    return {
        "event_key": own_row.get("event_key"),
        "event_date": own_row.get("event_date"),
        "team_id": team_id,
        "season": (event or {}).get("season"),
        "stat_line": own_row.get("stat_line") or {},
        "opponent_stat_line": (opponent_row or {}).get("stat_line") or {},
        "points_for": points_for,
        "points_against": points_against,
        "periods": (event or {}).get("periods"),
        "advanced": own_row.get("advanced"),
    }


def index_rows_by_event(team_game_stats: list[dict]) -> dict[str, list[dict]]:
    by_event: dict[str, list[dict]] = {}
    for row in team_game_stats:
        by_event.setdefault(row.get("event_key"), []).append(row)
    return by_event


def opponent_row(rows_by_event: dict[str, list[dict]], own_row: dict) -> dict | None:
    return next((r for r in rows_by_event.get(own_row.get("event_key"), []) if r.get("team_id") != own_row.get("team_id")), None)


def paired_rows(
    team_rows: list[dict], rows_by_event: dict[str, list[dict]], events_by_key: dict[str, dict],
) -> list[dict]:
    """team_rows (one team's own rows, most recent first) paired with their opponents' rows."""
    return [
        paired_row(events_by_key.get(row.get("event_key")), row, opponent_row(rows_by_event, row))
        for row in team_rows
    ]


def live_paired_history(
    storage, sport: str, team_id: str, before_date: str, team_game_stats: list[dict], events: list[dict], limit: int,
) -> list[dict]:
    """Serving: a team's last `limit` box rows before before_date, paired,
    from the team_game_stats and completed events already loaded."""
    rows = storage.get_team_game_stats_for_team(sport, team_id, before_date=before_date, limit=limit, team_game_stats=team_game_stats)
    return paired_rows(rows, index_rows_by_event(team_game_stats), {e["event_key"]: e for e in events})
