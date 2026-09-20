"""
Shared feature-engineering build_dataset.py boilerplate (nfl/nba/ncaafb/
ncaambb -- confirmed byte-identical before sharing here, aside from
ncaafb's own build_player_dataset, which additionally threads team
coordinates through build_player_features and stays local for that
reason). The actual event-row feature-building logic
(build_event_dataset/_build_event_row) stays fully per-sport throughout
-- NFL/NCAAFB track QB/RB/WR leader histories NBA/NCAAMBB have no
equivalent for.

Each sport's own build_dataset.py keeps `build_player_dataset`/
`_write_parquet`/`_lookback_since_date` under their original, tested
names (called directly by each sport's own test_build_dataset.py, no
attribute-patching involved), delegating to the functions below via thin
wrappers with their own SPORT/build_player_features/logger baked in.
"""
import io
import json
import os
from collections import defaultdict
from datetime import date, timedelta

import pandas as pd

from library.features.common import compute_elo_ratings


def group_player_games_by_player(player_games: list[dict]) -> dict[str, list[dict]]:
    by_player: dict[str, list[dict]] = defaultdict(list)
    for game in player_games:
        by_player[game["entity_id"]].append(game)
    for games in by_player.values():
        games.sort(key=lambda g: g.get("event_date", ""))
    return by_player


def team_previous_event_dates(events: list[dict]) -> dict[tuple[str, str], str | None]:
    """Maps (team_id, event_key) -> that team's previous event's date."""
    by_team: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        for participant in event.get("participants", []):
            by_team[participant["entity_id"]].append(event)

    previous_dates: dict[tuple[str, str], str | None] = {}
    for team_id, team_events in by_team.items():
        team_events.sort(key=lambda e: e.get("event_date", ""))
        previous_date = None
        for event in team_events:
            previous_dates[(team_id, event["event_key"])] = previous_date
            previous_date = event.get("event_date")
    return previous_dates


def index_team_game_stats(team_game_stats: list[dict]) -> dict[tuple[str, str], dict]:
    # One row per (event, team), keyed for direct lookup.
    return {(row["event_key"], row["team_id"]): row for row in team_game_stats}


def build_player_dataset(
    storage, sport: str, window: int, build_player_features_fn, logger,
    since_date: str | None = None, filter_fn=None,
) -> list[dict]:
    """Same incremental-history approach as each sport's own
    build_event_dataset, per player instead of per team. filter_fn (e.g.
    is_real_franchise_matchup) is optional -- nba/nfl exclude exhibition
    games this way, ncaambb doesn't need to (no exhibition concept)."""
    events = storage.get_all_events(sport, since_date=since_date)
    if filter_fn is not None:
        events = [e for e in events if filter_fn(e)]
    events_by_key = {event["event_key"]: event for event in events}
    elo_ratings, _ = compute_elo_ratings(events)  # only the pre-game side is used here
    previous_event_dates = team_previous_event_dates(events)

    player_games = storage.get_all_player_game_stats(sport, since_date=since_date)
    logger.info("Loaded %d player-game rows", len(player_games))

    games_by_player = group_player_games_by_player(player_games)

    total = len(player_games)
    seen = 0  # rows examined, including skipped ones
    skipped = 0
    rows = []
    for games in games_by_player.values():
        history: list[dict] = []  # ascending, grows as we go
        for game in games:
            event = events_by_key.get(game["event_key"])
            participants = event.get("participants", []) if event else []
            has_home_and_away = any(p.get("role") == "home" for p in participants) and any(
                p.get("role") == "away" for p in participants
            )
            if not has_home_and_away:
                logger.debug("Skipping player-game %s -- event missing or missing home/away role", game["event_key"])
                skipped += 1
            else:
                prior = history[-window:][::-1]  # most-recent-first, capped at window
                own_previous_event_date = previous_event_dates.get((game["team_id"], game["event_key"]))
                rows.append(build_player_features_fn(game, prior, event, elo_ratings, own_previous_event_date, window))
                history.append(game)

            seen += 1
            if seen % 20000 == 0 or seen == total:
                logger.info("Built player features: %d/%d (%d skipped)", seen, total, skipped)

    return rows


def write_parquet(rows: list[dict]) -> bytes:
    """JSON-encodes dict-valued columns (e.g. label_stat_line) before
    writing, since Parquet doesn't support raw dict values."""
    if not rows:
        return b""
    df = pd.DataFrame(rows)
    dict_columns = [key for key, value in rows[0].items() if isinstance(value, dict)]
    for col in dict_columns:
        df[col] = df[col].apply(json.dumps)
    buffer = io.BytesIO()
    df.to_parquet(buffer, engine="pyarrow", index=False)
    return buffer.getvalue()


def lookback_since_date() -> str | None:
    """Converts TRAINING_LOOKBACK_SEASONS (a season count) into an
    approximate since_date FeatureStorage's GSI queries can filter on --
    unset (the common case today) means unbounded, same as before this
    existed. 366 days/season is deliberately generous (never trims a
    genuinely in-window season for being a day short)."""
    lookback = os.environ.get("TRAINING_LOOKBACK_SEASONS")
    if not lookback:
        return None
    return (date.today() - timedelta(days=int(lookback) * 366)).isoformat()
