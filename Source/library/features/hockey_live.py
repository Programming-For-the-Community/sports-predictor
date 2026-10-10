"""
Live (serving-time) NHL feature rows, built with the same pure functions
(library.features.nhl) as the training datasets.

What serving has to work out for itself that training reads off the box
score:
- The starting goalie. In order: the scoreboard's probable goalie stored
  on the event ("Confirmed", else "Expected"), else a prediction -- the
  team's number-one goalie by starts this season, or the other goalie
  when the number one started yesterday. goalie_source on the row says
  which.
- A team's goalie tandem, rebuilt from its currently rostered goalies'
  own game logs. A goalie who has since left the team is not counted.

The lineup group is never built here (who dresses is unknown before a
game), so the game models are not trained on it either -- see
model-training/nhl/nhl_training.py.
"""
from datetime import date, timedelta

from library.features import nhl, nhl_dataset, nhl_teams
from library.features.common import DEFAULT_STARTING_RATING
from library.features.live_orchestration import EventNotFoundError, home_away_ids, top_n_by_recent_volume
from library.features.matchup import live_matchup_columns
from library.schema.keys import player_key

# Covers nhl.TEAM_HISTORY_GAMES of team box scores, off-season included.
TEAM_BOX_LOOKBACK_DAYS = 500
GOALIE_HISTORY_GAMES = 150
GOALIE_POSITION = "G"
CONFIRMED_STATUS = "Confirmed"

GOALIE_SOURCE_CONFIRMED = "confirmed"
GOALIE_SOURCE_PROBABLE = "probable"
GOALIE_SOURCE_PREDICTED = "predicted"

# Leaders panel: how many skaters per team are scored, picked by recent ice time.
LEADER_SKATER_LIMIT = 8
SKATER_VOLUME_STAT = "time_on_ice_seconds"


def _event(storage, event_key: str) -> dict:
    event = storage.get_event(event_key)
    if event is None:
        raise EventNotFoundError(f"No event found for {event_key}")
    return event


def _days_before(event_date: str, days: int) -> str:
    return (date.fromisoformat(event_date) - timedelta(days=days)).isoformat()


def live_elo_ratings(events: list[dict], event: dict, home_id: str, away_id: str) -> dict:
    """{event_key: pre-game ratings} for `event`, from the completed
    `events`: its own recorded pre-game ratings once it is in that
    history, else each franchise's current rating."""
    pre_game, current = nhl_dataset.franchise_elo_ratings(events, as_of_season=event.get("season"))
    key = event["event_key"]
    if key in pre_game:
        return {key: pre_game[key]}
    return {key: {
        "home_pre_rating": current.get(nhl_teams.franchise_id(home_id), DEFAULT_STARTING_RATING),
        "away_pre_rating": current.get(nhl_teams.franchise_id(away_id), DEFAULT_STARTING_RATING),
    }}


class _Context:
    """Everything one request reads once and reuses for both teams."""

    def __init__(self, storage, sport: str, event: dict, events: list[dict] | None = None) -> None:
        self.storage = storage
        self.sport = sport
        self.event = event
        self.before_date = event["event_date"]
        self.season = event.get("season")
        self.home_id, self.away_id = home_away_ids(event)
        self.events = events if events is not None else storage.get_all_events(sport)
        self.season_by_event = {e["event_key"]: e.get("season") for e in self.events}
        self.elo_ratings = live_elo_ratings(self.events, event, self.home_id, self.away_id)
        self._box_by_event_team: dict | None = None
        self._records: dict[str, list[dict]] = {}
        self._goalie_logs: dict[str, dict[str, list[dict]]] = {}
        self._starters: dict[str, tuple[str | None, str | None]] = {}

    def opponent_of(self, team_id: str) -> str:
        return self.away_id if team_id == self.home_id else self.home_id

    def role_of(self, team_id: str) -> str:
        return "home" if team_id == self.home_id else "away"

    def records(self, team_id: str) -> list[dict]:
        """The team's team_game_records before this event, most recent first."""
        if team_id not in self._records:
            if self._box_by_event_team is None:
                rows = self.storage.get_all_team_game_stats(
                    self.sport, since_date=_days_before(self.before_date, TEAM_BOX_LOOKBACK_DAYS),
                )
                self._box_by_event_team = {(row["event_key"], row["team_id"]): row for row in rows}
            box = self._box_by_event_team
            team_events = self.storage.get_team_events(
                self.sport, team_id, before_date=self.before_date, limit=nhl.TEAM_HISTORY_GAMES, events=self.events,
            )
            records = []
            for past in team_events:
                opponent = next((p["entity_id"] for p in past.get("participants", []) if p.get("entity_id") != team_id), None)
                record = nhl.team_game_record(
                    past, team_id, box.get((past["event_key"], team_id)), box.get((past["event_key"], opponent)),
                )
                if record is not None:
                    records.append(record)
            self._records[team_id] = records
        return self._records[team_id]

    def goalie_history(self, goalie_id: str) -> list[dict]:
        """One goalie's goalie_game_records before this event, most recent first."""
        rows = self.storage.get_player_game_stats(goalie_id, before_date=self.before_date, limit=GOALIE_HISTORY_GAMES)
        return [nhl.goalie_game_record(row, self.season_by_event.get(row.get("event_key"))) for row in rows]

    def goalie_logs(self, team_id: str) -> dict[str, list[dict]]:
        """{goalie id: history} for every goalie currently rostered to the team."""
        if team_id not in self._goalie_logs:
            goalie_ids = [
                entity["entity_id"] for entity in self.storage.get_team_entities(self.sport, team_id)
                if (entity.get("metadata") or {}).get("position") == GOALIE_POSITION
            ]
            self._goalie_logs[team_id] = {goalie_id: self.goalie_history(goalie_id) for goalie_id in goalie_ids}
        return self._goalie_logs[team_id]

    def _season_starts(self, team_id: str) -> dict[str, list[dict]]:
        """{goalie id: his records for this team this season}."""
        return {
            goalie_id: [r for r in history if r.get("season") == self.season and r.get("team_id") == team_id]
            for goalie_id, history in self.goalie_logs(team_id).items()
        }

    def _predicted_starter(self, team_id: str) -> str | None:
        starts = {
            goalie_id: [r for r in records if r.get("started")] for goalie_id, records in self._season_starts(team_id).items()
        }
        ranked = sorted((goalie_id for goalie_id in starts if starts[goalie_id]), key=lambda g: len(starts[g]), reverse=True)
        if not ranked:
            return next(iter(self.goalie_logs(team_id)), None)
        yesterday = _days_before(self.before_date, 1)
        number_one_started_yesterday = starts[ranked[0]][0].get("event_date") == yesterday
        return ranked[1] if number_one_started_yesterday and len(ranked) > 1 else ranked[0]

    def starter(self, team_id: str) -> tuple[str | None, str | None]:
        """(goalie id, source) -- see this module's docstring for the order."""
        if team_id not in self._starters:
            role = self.role_of(team_id)
            probable = self.event.get(f"{role}_probable_goalie_id")
            if probable:
                confirmed = self.event.get(f"{role}_probable_goalie_status") == CONFIRMED_STATUS
                self._starters[team_id] = (probable, GOALIE_SOURCE_CONFIRMED if confirmed else GOALIE_SOURCE_PROBABLE)
            else:
                predicted = self._predicted_starter(team_id)
                self._starters[team_id] = (predicted, GOALIE_SOURCE_PREDICTED if predicted else None)
        return self._starters[team_id]

    def goalie_features(self, team_id: str) -> dict | None:
        """The resolved starter's nhl.goalie_features, or None with no goalie."""
        goalie_id, _ = self.starter(team_id)
        return None if goalie_id is None else self.features_for(goalie_id, team_id)

    def features_for(self, goalie_id: str, team_id: str) -> dict:
        """nhl.goalie_features for goalie_id as team_id's starter."""
        logs = self.goalie_logs(team_id)
        history = logs[goalie_id] if goalie_id in logs else self.goalie_history(goalie_id)
        season_goalies = self._season_starts(team_id)
        if goalie_id not in season_goalies:
            season_goalies[goalie_id] = [r for r in history if r.get("season") == self.season and r.get("team_id") == team_id]
        starts = sorted(
            (record for records in season_goalies.values() for record in records if record.get("started")),
            key=lambda record: record.get("event_date") or "", reverse=True,
        )
        return nhl.goalie_features(
            goalie_id, history, [record["entity_id"] for record in starts], season_goalies, self.before_date, self.season,
        )

    def recent_lines(self, team_id: str) -> list[dict]:
        """The team's last nhl.LINEUP_WINDOW_GAMES skater lines, most recent first."""
        lines = []
        for record in self.records(team_id)[:nhl.LINEUP_WINDOW_GAMES]:
            rows = [row for row in self.storage.get_player_game_stats_for_event(record["event_key"]) if row.get("team_id") == team_id]
            lines.append(nhl.skater_game_line(rows))
        return lines


def build_live_event_features(storage, sport: str, event_key: str, events: list[dict] | None = None) -> dict:
    """One event-level feature row for event_key, in the shape the
    win-probability and score models were trained on, plus each side's
    resolved starter as {home,away}_goalie_id/{home,away}_goalie_source."""
    context = _Context(storage, sport, _event(storage, event_key), events)
    row = nhl.build_event_features(
        context.event, context.elo_ratings, context.records(context.home_id), context.records(context.away_id),
        home_goalie=context.goalie_features(context.home_id), away_goalie=context.goalie_features(context.away_id),
    )
    for role, team_id in (("home", context.home_id), ("away", context.away_id)):
        row[f"{role}_goalie_id"], row[f"{role}_goalie_source"] = context.starter(team_id)
    return row


def _player_game(sport: str, event: dict, entity_id: str, team_id: str, position: str | None) -> dict:
    return {
        "event_key": event["event_key"], "player_key": player_key(sport, entity_id), "entity_id": entity_id,
        "team_id": team_id, "event_date": event["event_date"], "position": position, "stat_line": {},
    }


def _skater_row(context: _Context, entity_id: str, team_id: str, position: str | None, prior_games: list[dict], lines: list[dict]) -> dict:
    opponent_id = context.opponent_of(team_id)
    player_game = _player_game(context.sport, context.event, entity_id, team_id, position)
    row = nhl.build_player_features(
        player_game, prior_games, context.event, context.elo_ratings,
        context.records(team_id), context.records(opponent_id),
        opponent_goalie=context.goalie_features(opponent_id), recent_lines=lines,
    )
    row.update(live_matchup_columns(
        context.storage, context.sport, player_game, context.home_id, context.away_id, prior_games, nhl.PLAYER_WINDOW,
        context.events,
    ))
    return row


def _goalie_row(context: _Context, entity_id: str, team_id: str) -> dict:
    player_game = {**_player_game(context.sport, context.event, entity_id, team_id, GOALIE_POSITION), "started": True}
    return nhl.build_goalie_features(
        player_game, context.event, context.elo_ratings, context.features_for(entity_id, team_id),
        context.records(team_id), context.records(context.opponent_of(team_id)),
    )


def build_live_player_features(
    storage, sport: str, event_key: str, entity_id: str, events: list[dict] | None = None,
) -> dict:
    """One player-prop feature row for entity_id in event_key: a goalie
    row for a goalie, a skater row otherwise. team_id and position come
    from the player's own entity record."""
    context = _Context(storage, sport, _event(storage, event_key), events)
    entity = storage.get_entity(sport, entity_id, "player")
    if entity is None:
        raise EventNotFoundError(f"No entity found for {entity_id}")
    metadata = entity.get("metadata") or {}
    team_id, position = metadata.get("team_id"), metadata.get("position")
    if position == GOALIE_POSITION:
        return _goalie_row(context, entity_id, team_id)
    prior_games = storage.get_player_game_stats(entity_id, before_date=context.before_date, limit=nhl.SKATER_HISTORY_GAMES)
    return _skater_row(context, entity_id, team_id, position, prior_games, context.recent_lines(team_id))


def build_live_event_leader_candidates(storage, sport: str, event_key: str, events: list[dict] | None = None) -> dict:
    """Feature rows for the players each team's leaders panel scores:
    {"home": {"skaters": [row, ...], "goalie": row | None}, "away": {...}}.
    Skaters are the team's rostered skaters with the most recent ice
    time; the goalie is the resolved starter."""
    context = _Context(storage, sport, _event(storage, event_key), events)

    def team_candidates(team_id: str) -> dict:
        positions = {
            entity["entity_id"]: (entity.get("metadata") or {}).get("position")
            for entity in storage.get_team_entities(sport, team_id)
        }
        skater_ids = [entity_id for entity_id, position in positions.items() if position != GOALIE_POSITION]
        top = top_n_by_recent_volume(
            storage, skater_ids, SKATER_VOLUME_STAT, context.before_date, nhl.PLAYER_WINDOW, LEADER_SKATER_LIMIT,
        )
        lines = context.recent_lines(team_id)
        skaters = []
        for entity_id in top:
            prior_games = storage.get_player_game_stats(entity_id, before_date=context.before_date, limit=nhl.SKATER_HISTORY_GAMES)
            skaters.append(_skater_row(context, entity_id, team_id, positions[entity_id], prior_games, lines))
        goalie_id, _ = context.starter(team_id)
        return {"skaters": skaters, "goalie": _goalie_row(context, goalie_id, team_id) if goalie_id else None}

    return {"home": team_candidates(context.home_id), "away": team_candidates(context.away_id)}
