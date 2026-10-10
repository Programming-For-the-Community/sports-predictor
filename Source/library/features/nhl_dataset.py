"""
Builds the NHL training rows in one chronological pass over a season
history: every team's, goalie's, skater's and lineup's own history grows
one game at a time, so each row only ever sees games before it.

Pandas-free, like library.features.nhl -- feature-engineering/nhl/
build_dataset.py owns loading the inputs and writing the Parquet files.

Team history is keyed by franchise (a relocated team keeps its history);
geography and alignment lookups keep the id the team played under.
"""
from collections import defaultdict

from library.features import matchup, nhl, nhl_teams
from library.features.common import compute_elo_ratings

LOG_INTERVAL = 2000
SPORT = "nhl"


def _home_away_ids(event: dict) -> tuple[str, str] | None:
    participants = event.get("participants", [])
    home = next((p for p in participants if p.get("role") == "home"), None)
    away = next((p for p in participants if p.get("role") == "away"), None)
    return (home["entity_id"], away["entity_id"]) if home is not None and away is not None else None


def franchise_elo_ratings(events: list[dict], as_of_season: int | None = None) -> tuple[dict, dict]:
    """compute_elo_ratings with the hockey constants, rated per franchise.
    Returns (pre-game ratings by event_key, current rating by franchise id)."""
    franchise_events = [
        {**event, "participants": [
            {**p, "entity_id": nhl_teams.franchise_id(p.get("entity_id"))} for p in event.get("participants", [])
        ]}
        for event in events
    ]
    return compute_elo_ratings(
        franchise_events, k_factor=nhl.ELO_K_FACTOR, home_advantage=nhl.ELO_HOME_ADVANTAGE,
        season_carryover=nhl.ELO_SEASON_CARRYOVER, as_of_season=as_of_season,
    )


def _starting_goalie(players: list[dict]) -> dict | None:
    return next((g for g in players if g.get("position_group") == "goalie" and g.get("started")), None)


class _History:
    """Everything known about every team and goalie before the game
    currently being scored."""

    def __init__(self) -> None:
        self.team_records: dict[str, list[dict]] = defaultdict(list)
        self.team_lines: dict[str, list[dict]] = defaultdict(list)
        self.goalie_records: dict[str, list[dict]] = defaultdict(list)
        self.skater_games: dict[str, list[dict]] = defaultdict(list)
        # Keyed by (franchise id, season).
        self.team_starters: dict[tuple, list[str | None]] = defaultdict(list)
        self.team_goalies: dict[tuple, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))

    def records(self, team_id: str) -> list[dict]:
        """Most recent first, capped at nhl.TEAM_HISTORY_GAMES."""
        return self.team_records[nhl_teams.franchise_id(team_id)][-nhl.TEAM_HISTORY_GAMES:][::-1]

    def lines(self, team_id: str) -> list[dict]:
        """The team's recent skater lines, most recent first."""
        return self.team_lines[nhl_teams.franchise_id(team_id)][-nhl.LINEUP_WINDOW_GAMES:][::-1]

    def lineup(self, team_id: str, players: list[dict]) -> dict:
        return nhl.lineup_features(self.lines(team_id), set(nhl.skater_game_line(players)) if players else None)

    def skater(self, player_id: str) -> list[dict]:
        """Most recent first, capped at nhl.SKATER_HISTORY_GAMES."""
        return self.skater_games[player_id][-nhl.SKATER_HISTORY_GAMES:][::-1]

    def goalie(self, team_id: str, goalie_id: str, event: dict) -> dict:
        team_season = (nhl_teams.franchise_id(team_id), event.get("season"))
        return nhl.goalie_features(
            goalie_id, self.goalie_records[goalie_id][::-1], self.team_starters[team_season][::-1],
            self.team_goalies[team_season], event["event_date"], event.get("season"),
        )

    def fold(self, event: dict, team_id: str, record: dict | None, players: list[dict]) -> None:
        """Adds this game to the team's and its goalies' histories."""
        franchise = nhl_teams.franchise_id(team_id)
        if record is not None:
            self.team_records[franchise].append(record)
        if not players:
            return
        team_season = (franchise, event.get("season"))
        self.team_lines[franchise].append(nhl.skater_game_line(players))
        starter = _starting_goalie(players)
        self.team_starters[team_season].append(starter["entity_id"] if starter else None)
        for game in players:
            if game.get("position_group") != "goalie":
                self.skater_games[game["entity_id"]].append(game)
                continue
            goalie_record = nhl.goalie_game_record(game, event.get("season"))
            self.goalie_records[game["entity_id"]].append(goalie_record)
            self.team_goalies[team_season][game["entity_id"]].append(goalie_record)


def _skater_rows(
    sides: dict, side: str, other: str, event: dict, elo_ratings: dict, history: _History, lookup,
) -> list[dict]:
    """One row per skater who played for `side`."""
    team = sides[side]
    lines = history.lines(team["team_id"])
    played = nhl.skater_game_line(team["players"])
    rows = []
    for game in team["players"]:
        if game["entity_id"] not in played:
            continue
        prior = history.skater(game["entity_id"])
        row = nhl.build_player_features(
            game, prior, event, elo_ratings, team["records"], sides[other]["records"],
            opponent_goalie=sides[other]["goalie"], recent_lines=lines,
        )
        row.update(matchup.matchup_columns(SPORT, game, sides[other]["team_id"], prior, lookup, nhl.PLAYER_WINDOW))
        rows.append(row)
    return rows


_SIDE_PAIRS = (("home", "away"), ("away", "home"))


def _side(history: _History, event: dict, team_id: str, players: list[dict]) -> dict:
    """One team's inputs for this game, from its history before it."""
    starter = _starting_goalie(players)
    return {
        "team_id": team_id,
        "players": players,
        "starter": starter,
        "records": history.records(team_id),
        "goalie": history.goalie(team_id, starter["entity_id"], event) if starter else None,
        "lineup": history.lineup(team_id, players),
    }


def _goalie_rows(sides: dict, side: str, other: str, event: dict, elo_ratings: dict) -> list[dict]:
    """The starting goalie's row for `side`, when it has one."""
    team = sides[side]
    if team["starter"] is None:
        return []
    return [nhl.build_goalie_features(
        team["starter"], event, elo_ratings, team["goalie"], team["records"], sides[other]["records"],
    )]


def build_datasets(
    events: list[dict], team_game_stats: list[dict], player_games: list[dict], logger,
) -> tuple[list[dict], list[dict], list[dict]]:
    """(event rows, goalie rows, skater rows) for every completed
    franchise matchup in `events`. One goalie row per starting goalie per
    game, one skater row per skater who played."""
    events = sorted(
        (e for e in events if nhl_teams.is_real_franchise_matchup(e) and _home_away_ids(e) is not None),
        key=lambda e: (e.get("event_date", ""), e.get("kickoff_time") or ""),
    )
    elo_ratings, _ = franchise_elo_ratings(events)
    box_by_event_team = {(row["event_key"], row["team_id"]): row for row in team_game_stats}
    players_by_event_team: dict[tuple, list[dict]] = defaultdict(list)
    for game in player_games:
        players_by_event_team[(game["event_key"], game["team_id"])].append(game)

    lookup = matchup.InMemoryMatchupLookup(events, player_games)
    history = _History()
    event_rows: list[dict] = []
    goalie_rows: list[dict] = []
    skater_rows: list[dict] = []
    total = len(events)
    for i, event in enumerate(events, start=1):
        home_id, away_id = _home_away_ids(event)
        key = event["event_key"]
        sides = {
            side: _side(history, event, team_id, players_by_event_team.get((key, team_id), []))
            for side, team_id in (("home", home_id), ("away", away_id))
        }

        event_rows.append(nhl.build_event_features(
            event, elo_ratings, sides["home"]["records"], sides["away"]["records"],
            home_goalie=sides["home"]["goalie"], away_goalie=sides["away"]["goalie"],
            home_lineup=sides["home"]["lineup"], away_lineup=sides["away"]["lineup"],
        ))
        for side, other in _SIDE_PAIRS:
            goalie_rows.extend(_goalie_rows(sides, side, other, event, elo_ratings))
            skater_rows.extend(_skater_rows(sides, side, other, event, elo_ratings, history, lookup))

        for side, other in _SIDE_PAIRS:
            team_id = sides[side]["team_id"]
            record = nhl.team_game_record(
                event, team_id, box_by_event_team.get((key, team_id)),
                box_by_event_team.get((key, sides[other]["team_id"])),
            )
            history.fold(event, team_id, record, sides[side]["players"])

        if i % LOG_INTERVAL == 0 or i == total:
            logger.info(
                "Built event features: %d/%d (%d goalie rows, %d skater rows)", i, total, len(goalie_rows), len(skater_rows),
            )

    return event_rows, goalie_rows, skater_rows
