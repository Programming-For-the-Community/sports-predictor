"""
Assembles a live feature vector for one not-yet-played NFL event or one
player-prop target, using the exact same pure functions
(build_event_features/build_player_features, library/features/nfl.py)
that build the training datasets -- this is the "later" half of the
train/serve-skew guarantee those functions' own docstrings describe.

The difference from feature-engineering's build_dataset.py is entirely
in how history gets gathered: batch feature engineering loads the whole
table once and walks it incrementally (cheap when you're building
thousands of rows); this module looks up exactly one event's worth of
context via FeatureStorage's one-team/one-player/one-event methods
(get_team_events, get_player_game_stats, get_player_game_stats_for_event,
get_team_game_stats_for_team, get_event, get_entity) -- cheap when you're
only ever building one row per request. Recomputes Elo from the full
completed-events history on every call rather than caching a "current
rating" snapshot -- NFL's ~2,700 completed games is the same cost
build_dataset.py already treats as cheap-enough-to-scan, and this project
has no request volume that would make that recomputation a real cost.
"""
from library.features.nfl import (
    DEFAULT_STARTING_RATING,
    build_event_features,
    build_player_features,
    compute_elo_ratings,
    identify_lead_receiver,
    identify_lead_rusher,
    identify_starting_qb,
    identify_top_receivers,
    identify_top_rushers,
    rank_by_average_stat,
)
from library.schema.keys import player_key

DEFAULT_ROLLING_WINDOW = 5


class EventNotFoundError(Exception):
    pass


class MalformedEventError(Exception):
    """The event exists but is missing a home/away participant role --
    build_event_features/build_player_features both require both sides
    to determine home/away, opponent, and Elo lookups."""


def _home_away_ids(event: dict) -> tuple[str, str]:
    participants = event.get("participants", [])
    home = next((p for p in participants if p.get("role") == "home"), None)
    away = next((p for p in participants if p.get("role") == "away"), None)
    if home is None or away is None:
        raise MalformedEventError(f"Event {event.get('event_key')} is missing a home or away participant")
    return home["entity_id"], away["entity_id"]


def _live_elo_ratings(
    storage, sport: str, event: dict, home_id: str, away_id: str, current_ratings: dict | None = None,
) -> dict:
    """A minimal elo_ratings dict containing just this one (not-yet-played)
    event's key, mapped to each team's CURRENT rating -- the second
    compute_elo_ratings return value, not the first. The first
    (pre_game_ratings) only has entries for events already processed, so
    it has nothing for a future event's own key; build_event_features/
    build_player_features only ever do one lookup
    (elo_ratings.get(event['event_key'])), so a single-entry dict works
    identically to the full training-time one for this purpose.

    current_ratings lets a caller that's already computed it (handler.py's
    _leaderboards, which calls this once per candidate player per prop
    stat) pass it straight through instead of paying this module's usual
    per-call full-history recompute -- see this module's docstring for why
    that recompute is normally cheap enough not to matter, and why a
    hundreds-of-candidates loop is exactly the condition that stops being
    true."""
    if current_ratings is None:
        completed_events = storage.get_all_events(sport)
        _, current_ratings = compute_elo_ratings(completed_events)
    return {
        event["event_key"]: {
            "home_pre_rating": current_ratings.get(home_id, DEFAULT_STARTING_RATING),
            "away_pre_rating": current_ratings.get(away_id, DEFAULT_STARTING_RATING),
        }
    }


def _presumptive_leader_and_history(
    storage, sport: str, team_id: str, before_date: str, identify_fn, window: int,
) -> list[dict]:
    """A future game has no player_game_stats of its own yet to identify
    a starting QB/lead rusher/lead receiver from (identify_starting_qb et
    al. all work by finding whoever had the most volume IN a given game).
    Approximates "who's likely to play that role next" as "whoever held
    it last game" -- pulls that team's most recent completed event, the
    stat lines from it, identifies the leader the same way training does,
    then returns that player's own rolling history. Empty list (not None)
    when no prior event or no identifiable leader exists, matching
    build_event_features' own home_qb_games=None-or-list contract."""
    last_events = storage.get_team_events(sport, team_id, before_date=before_date, limit=1)
    if not last_events:
        return []
    last_event_players = storage.get_player_game_stats_for_event(last_events[0]["event_key"])
    team_players = [row for row in last_event_players if row.get("team_id") == team_id]
    leader = identify_fn(team_players)
    if leader is None:
        return []
    return storage.get_player_game_stats(leader["entity_id"], before_date=before_date, limit=window)


def build_live_event_features(storage, sport: str, event_key: str, window: int = DEFAULT_ROLLING_WINDOW) -> dict:
    """One event-level feature row for event_key, in the exact shape
    train_win_probability_model.py/train_score_model.py were trained on (label_* fields
    will be None/absent for an unplayed game -- callers only read the
    feature columns off the result, never the labels)."""
    event = storage.get_event(event_key)
    if event is None:
        raise EventNotFoundError(f"No event found for {event_key}")
    home_id, away_id = _home_away_ids(event)

    home_events = storage.get_team_events(sport, home_id, before_date=event["event_date"], limit=window)
    away_events = storage.get_team_events(sport, away_id, before_date=event["event_date"], limit=window)
    home_box = storage.get_team_game_stats_for_team(home_id, before_date=event["event_date"], limit=window)
    away_box = storage.get_team_game_stats_for_team(away_id, before_date=event["event_date"], limit=window)

    return build_event_features(
        event,
        _live_elo_ratings(storage, sport, event, home_id, away_id),
        home_events,
        away_events,
        window,
        home_qb_games=_presumptive_leader_and_history(
            storage, sport, home_id, event["event_date"], identify_starting_qb, window),
        away_qb_games=_presumptive_leader_and_history(
            storage, sport, away_id, event["event_date"], identify_starting_qb, window),
        home_rb_games=_presumptive_leader_and_history(
            storage, sport, home_id, event["event_date"], identify_lead_rusher, window),
        away_rb_games=_presumptive_leader_and_history(
            storage, sport, away_id, event["event_date"], identify_lead_rusher, window),
        home_wr_games=_presumptive_leader_and_history(
            storage, sport, home_id, event["event_date"], identify_lead_receiver, window),
        away_wr_games=_presumptive_leader_and_history(
            storage, sport, away_id, event["event_date"], identify_lead_receiver, window),
        home_team_box_stats=home_box,
        away_team_box_stats=away_box,
    )


def _build_player_feature_row(
    storage, sport: str, event: dict, home_id: str, away_id: str, entity_id: str, team_id: str,
    prior_games: list[dict], window: int,
    current_ratings: dict | None = None, team_last_event_dates: dict[str, str] | None = None,
) -> dict:
    """Shared by build_live_player_features (one specific requested
    player) and build_live_event_leader_candidates (every candidate
    likely to lead a team in some category) -- both already know
    team_id and (for the leader case) may have already fetched
    prior_games while identifying the candidate in the first place, so
    this takes them as arguments rather than re-deriving them itself.

    team_last_event_dates, like current_ratings, is an optional
    already-computed lookup (handler.py's _leaderboards builds one team's
    worth of "last completed game date" once from the same completed-events
    fetch _season_standings_inputs already does) -- when given, this skips
    its own storage.get_team_events call, the other per-candidate query
    this function would otherwise repeat once per player per stat."""
    if team_last_event_dates is not None:
        own_previous_event_date = team_last_event_dates.get(team_id)
    else:
        own_previous_events = storage.get_team_events(sport, team_id, before_date=event["event_date"], limit=1)
        own_previous_event_date = own_previous_events[0]["event_date"] if own_previous_events else None

    player_game = {
        "event_key": event["event_key"],
        "player_key": player_key(sport, entity_id),
        "entity_id": entity_id,
        "team_id": team_id,
        "event_date": event["event_date"],
        "stat_line": {},  # unknown -- this is what's being predicted, not labeled
    }

    return build_player_features(
        player_game,
        prior_games,
        event,
        _live_elo_ratings(storage, sport, event, home_id, away_id, current_ratings),
        own_previous_event_date,
        window,
    )


def build_live_player_features(
    storage, sport: str, event_key: str, entity_id: str, window: int = DEFAULT_ROLLING_WINDOW,
    current_ratings: dict | None = None, team_last_event_dates: dict[str, str] | None = None,
) -> dict:
    """One player-prop feature row for entity_id's performance in
    event_key, in the exact shape train_player_prop_model.py was trained
    on. team_id comes from the player's own entity record (metadata.team_id,
    kept current by every normalize.py upsert), not from the event or
    their own last game log, since a just-traded player's most recent
    game log would still show their old team.

    current_ratings/team_last_event_dates: see _build_player_feature_row --
    optional pass-throughs for a caller (handler.py's season leaderboards)
    that's calling this in a loop over many players and has already paid
    for the full-history data these would otherwise each re-fetch."""
    event = storage.get_event(event_key)
    if event is None:
        raise EventNotFoundError(f"No event found for {event_key}")
    home_id, away_id = _home_away_ids(event)

    entity = storage.get_entity(sport, entity_id)
    if entity is None:
        raise EventNotFoundError(f"No entity found for {entity_id}")
    team_id = entity.get("metadata", {}).get("team_id")

    prior_games = storage.get_player_game_stats(entity_id, before_date=event["event_date"], limit=window)
    return _build_player_feature_row(
        storage, sport, event, home_id, away_id, entity_id, team_id, prior_games, window,
        current_ratings, team_last_event_dates,
    )


def build_live_event_leader_candidates(
    storage, sport: str, event_key: str, window: int = DEFAULT_ROLLING_WINDOW,
) -> dict:
    """One feature row per candidate likely to lead each team in
    passing/receiving/rushing/sacks for event_key, grouped
    {"home": {"passing": [row], "receiving": [row, row, row],
    "rushing": [row, row], "sacks": [row, row, row]}, "away": {...}} --
    each row is build_player_features' usual output (carries entity_id),
    same as build_live_player_features. Deliberately doesn't touch S3 or
    load any model -- scoring these against the right player-prop model
    per category is the caller's job (handler.py), same separation
    build_live_player_features/model_loader.py already have.

    Computes current_ratings ONCE here and threads it through every
    candidate (up to ~15 per event: QB + 3 receivers + 2 rushers + 3
    pass-rushers, per team) -- without this, each candidate's own
    _build_player_feature_row call would separately pay _live_elo_ratings'
    full-history recompute (see that function's docstring for why a loop
    like this is exactly the condition that stops being cheap). Confirmed
    live via CloudWatch: /nfl/predictions/events/{id} requests were
    hitting the full 29s Lambda timeout before this fix, the same failure
    mode _leaderboards (handler.py's /nfl/season route) had until it got
    the identical treatment -- this was the one remaining call site that
    hadn't."""
    event = storage.get_event(event_key)
    if event is None:
        raise EventNotFoundError(f"No event found for {event_key}")
    home_id, away_id = _home_away_ids(event)

    _, current_ratings = compute_elo_ratings(storage.get_all_events(sport))

    return {
        "home": _team_leader_candidates(storage, sport, event, home_id, away_id, home_id, window, current_ratings),
        "away": _team_leader_candidates(storage, sport, event, home_id, away_id, away_id, window, current_ratings),
    }


def _team_leader_candidates(
    storage, sport: str, event: dict, home_id: str, away_id: str, team_id: str, window: int,
    current_ratings: dict | None = None,
) -> dict:
    before_date = event["event_date"]
    last_events = storage.get_team_events(sport, team_id, before_date=before_date, limit=1)
    if not last_events:
        return {"passing": [], "receiving": [], "rushing": [], "sacks": []}

    # This team's own previous-game date is already known from last_events
    # above -- reusing it here (instead of letting each candidate's own
    # _build_player_feature_row call separately re-derive it via its own
    # storage.get_team_events call) is the second half of this function's
    # fix, alongside current_ratings.
    team_last_event_dates = {team_id: last_events[0]["event_date"]}

    last_event_players = storage.get_player_game_stats_for_event(last_events[0]["event_key"])
    team_players = [row for row in last_event_players if row.get("team_id") == team_id]

    def rows_for(candidates: list[dict]) -> list[dict]:
        rows = []
        for candidate in candidates:
            entity_id = candidate["entity_id"]
            prior_games = storage.get_player_game_stats(entity_id, before_date=before_date, limit=window)
            rows.append(_build_player_feature_row(
                storage, sport, event, home_id, away_id, entity_id, team_id, prior_games, window,
                current_ratings, team_last_event_dates,
            ))
        return rows

    qb = identify_starting_qb(team_players)
    passing_rows = rows_for([qb]) if qb else []
    receiving_rows = rows_for(identify_top_receivers(team_players))
    rushing_rows = rows_for(identify_top_rushers(team_players))

    # Sacks: ranked by each candidate's OWN rolling average, not
    # single-game volume -- see rank_by_average_stat's docstring. Every
    # player on the last game's roster is a candidate; offensive players
    # naturally sort to the bottom since they have no defensive_sacks
    # history, so no separate position filter is needed.
    histories = {
        row["entity_id"]: storage.get_player_game_stats(row["entity_id"], before_date=before_date, limit=window)
        for row in team_players
    }
    top_sack_ids = rank_by_average_stat(histories, "defensive_sacks", 3)
    sacks_rows = [
        _build_player_feature_row(
            storage, sport, event, home_id, away_id, entity_id, team_id, histories[entity_id], window,
            current_ratings, team_last_event_dates,
        )
        for entity_id in top_sack_ids
    ]

    return {"passing": passing_rows, "receiving": receiving_rows, "rushing": rushing_rows, "sacks": sacks_rows}
