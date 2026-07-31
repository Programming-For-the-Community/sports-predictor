"""
Pure NFL feature-computation functions, shared between the batch Fargate
feature-engineering task (building a training dataset from full history)
and, later, the inference Lambda (building a live feature vector for one
upcoming matchup) -- see design/CLAUDE.md's adapter interface, which names
this step build_features(). No AWS calls live here; every function takes
already-fetched rows (from FeatureStorage) and returns numbers. Keeping
train-time and serve-time feature computation as the same functions is
what prevents train/serve skew -- if this logic were duplicated instead of
shared, the model could end up trained on features computed slightly
differently than what it sees at prediction time.

Not covered here: season win totals, Super Bowl winner, and playoff game
winners. Those aren't separate feature sets -- they're produced by
simulating a season/bracket using the per-game outcome model's win
probabilities repeatedly (a model/predict.py concern), built entirely on
top of the event-level features below.
"""
from datetime import date

DEFAULT_ROLLING_WINDOW = 5
DEFAULT_STARTING_RATING = 1500.0
DEFAULT_K_FACTOR = 20.0
DEFAULT_HOME_ADVANTAGE = 55.0


def compute_elo_ratings(
    events: list[dict],
    k_factor: float = DEFAULT_K_FACTOR,
    home_advantage: float = DEFAULT_HOME_ADVANTAGE,
    starting_rating: float = DEFAULT_STARTING_RATING,
) -> dict[str, dict[str, float]]:
    """Walks head-to-head events in chronological order, updating a running
    Elo-style rating per team. Returns each event's PRE-game ratings, keyed
    by event_key -- using the post-game rating would leak that event's own
    outcome into its own features.

    Plain win/loss Elo, no margin-of-victory scaling (see design/
    PROJECT_PLAN.md Phase 1's "an Elo-style rating" bullet -- this is
    intentionally the simple version). Ties count as a 0.5 result for both
    teams. Events without both a home/away role or a final score still get
    a pre-game rating recorded, they just don't produce a rating update.
    """
    ratings: dict[str, float] = {}
    pre_game_ratings: dict[str, dict[str, float]] = {}

    for event in sorted(events, key=lambda e: e["event_date"]):
        participants = event.get("participants", [])
        home = next((p for p in participants if p.get("role") == "home"), None)
        away = next((p for p in participants if p.get("role") == "away"), None)
        if home is None or away is None:
            continue

        home_id, away_id = home["entity_id"], away["entity_id"]
        home_rating = ratings.setdefault(home_id, starting_rating)
        away_rating = ratings.setdefault(away_id, starting_rating)

        pre_game_ratings[event["event_key"]] = {
            "home_pre_rating": home_rating,
            "away_pre_rating": away_rating,
        }

        home_score = home.get("result", {}).get("score")
        away_score = away.get("result", {}).get("score")
        if home_score is None or away_score is None:
            continue

        if home_score > away_score:
            home_actual, away_actual = 1.0, 0.0
        elif home_score < away_score:
            home_actual, away_actual = 0.0, 1.0
        else:
            home_actual = away_actual = 0.5

        expected_home = 1 / (1 + 10 ** ((away_rating - (home_rating + home_advantage)) / 400))
        expected_away = 1 - expected_home

        ratings[home_id] = home_rating + k_factor * (home_actual - expected_home)
        ratings[away_id] = away_rating + k_factor * (away_actual - expected_away)

    return pre_game_ratings


def rest_days(event_date: str, previous_event_date: str | None) -> int | None:
    """Days between a team's previous game and this one. None if there's no
    previous game in the given history (e.g. a team's first game in the
    dataset)."""
    if previous_event_date is None:
        return None
    return (date.fromisoformat(event_date) - date.fromisoformat(previous_event_date)).days


def rolling_team_scoring_averages(
    team_events: list[dict], entity_id: str, window: int = DEFAULT_ROLLING_WINDOW
) -> dict:
    """team_events: a team's own completed events, most recent first (see
    FeatureStorage.get_team_events), NOT including the event being scored.
    Averages points scored/allowed over up to the last `window` games;
    None for either average if the team has no qualifying history yet."""
    scored, allowed = [], []
    for event in team_events[:window]:
        participants = event.get("participants", [])
        own = next((p for p in participants if p.get("entity_id") == entity_id), None)
        opponent = next((p for p in participants if p.get("entity_id") != entity_id), None)
        if own is None or opponent is None:
            continue
        own_score = own.get("result", {}).get("score")
        opp_score = opponent.get("result", {}).get("score")
        if own_score is None or opp_score is None:
            continue
        scored.append(own_score)
        allowed.append(opp_score)

    return {
        "avg_points_scored": sum(scored) / len(scored) if scored else None,
        "avg_points_allowed": sum(allowed) / len(allowed) if allowed else None,
        "games_played": len(scored),
    }


def rolling_player_stat_averages(
    player_games: list[dict], window: int = DEFAULT_ROLLING_WINDOW
) -> dict:
    """player_games: a player's own completed games, most recent first (see
    FeatureStorage.get_player_game_stats), NOT including the game being
    scored. For every stat_line key that appears in at least one of the
    last `window` games, averages it over only the games that have that
    key -- a QB's passing keys and a kicker's field-goal keys never appear
    on the same player, so this generically covers every position's stat
    categories without a per-position list.

    Usage rate (share of team volume, e.g. target share) is NOT computed
    here -- that needs every player on the team's stat line for the same
    game to compute a team total, which this function doesn't have. Raw
    per-game volume counts (attempts, targets, carries) are covered as
    ordinary stat_line keys above; a true share metric is a follow-up.
    """
    windowed = player_games[:window]
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}

    for game in windowed:
        for key, value in game.get("stat_line", {}).items():
            if not isinstance(value, (int, float)):
                continue
            totals[key] = totals.get(key, 0) + value
            counts[key] = counts.get(key, 0) + 1

    averages = {f"avg_{key}": totals[key] / counts[key] for key in totals}
    averages["games_played"] = len(windowed)
    averages["starts"] = sum(1 for game in windowed if game.get("started"))
    return averages


def build_event_features(
    event: dict,
    elo_ratings: dict[str, dict[str, float]],
    home_team_events: list[dict],
    away_team_events: list[dict],
    window: int = DEFAULT_ROLLING_WINDOW,
) -> dict:
    """Assembles one training row for a head-to-head event: game-outcome
    (win/loss) and game-score features and labels share this same row,
    since both targets are trained from the same inputs.

    elo_ratings is compute_elo_ratings' full output. home_team_events/
    away_team_events are each team's own prior completed events, most
    recent first, NOT including this one (see FeatureStorage.get_team_events
    with before_date=event['event_date']).
    """
    participants = event["participants"]
    home = next(p for p in participants if p.get("role") == "home")
    away = next(p for p in participants if p.get("role") == "away")
    home_id, away_id = home["entity_id"], away["entity_id"]

    ratings = elo_ratings.get(event["event_key"], {})
    home_elo = ratings.get("home_pre_rating")
    away_elo = ratings.get("away_pre_rating")

    home_scoring = rolling_team_scoring_averages(home_team_events, home_id, window)
    away_scoring = rolling_team_scoring_averages(away_team_events, away_id, window)

    return {
        "event_key": event["event_key"],
        "event_date": event["event_date"],
        "home_entity_id": home_id,
        "away_entity_id": away_id,
        "home_elo": home_elo,
        "away_elo": away_elo,
        "elo_diff": (home_elo - away_elo) if home_elo is not None and away_elo is not None else None,
        "home_rest_days": rest_days(event["event_date"], home_team_events[0]["event_date"]) if home_team_events else None,
        "away_rest_days": rest_days(event["event_date"], away_team_events[0]["event_date"]) if away_team_events else None,
        "home_avg_points_scored": home_scoring["avg_points_scored"],
        "home_avg_points_allowed": home_scoring["avg_points_allowed"],
        "home_games_played": home_scoring["games_played"],
        "away_avg_points_scored": away_scoring["avg_points_scored"],
        "away_avg_points_allowed": away_scoring["avg_points_allowed"],
        "away_games_played": away_scoring["games_played"],
        # Labels -- the training targets (win/loss and final score), not
        # model inputs. None when this is called to build a live feature
        # vector for a not-yet-played event.
        "label_home_won": home.get("result", {}).get("won"),
        "label_home_score": home.get("result", {}).get("score"),
        "label_away_score": away.get("result", {}).get("score"),
    }


def build_player_features(
    player_game: dict, prior_games: list[dict], window: int = DEFAULT_ROLLING_WINDOW
) -> dict:
    """Assembles one training row for a player-prop target: player_game is
    the specific game being labeled (its stat_line becomes the training
    label); prior_games is that player's own completed games before this
    one, most recent first (see FeatureStorage.get_player_game_stats with
    before_date=player_game['event_date'])."""
    averages = rolling_player_stat_averages(prior_games, window)
    return {
        "event_key": player_game["event_key"],
        "player_key": player_game["player_key"],
        "entity_id": player_game["entity_id"],
        "team_id": player_game["team_id"],
        "event_date": player_game["event_date"],
        **averages,
        # Label -- this game's actual stat line, the training target.
        "label_stat_line": player_game.get("stat_line", {}),
        "label_started": player_game.get("started"),
    }
