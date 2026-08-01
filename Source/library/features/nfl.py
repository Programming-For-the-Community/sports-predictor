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
import logging
import math
from datetime import date

from library.features.nfl_teams import INTERNATIONAL_VENUES, is_divisional_game, is_international_game, travel_distances_km

logger = logging.getLogger(__name__)

DEFAULT_ROLLING_WINDOW = 5
DEFAULT_STARTING_RATING = 1500.0
DEFAULT_K_FACTOR = 20.0
DEFAULT_HOME_ADVANTAGE = 55.0
# FiveThirtyEight's NFL Elo margin-of-victory constant -- see _mov_multiplier.
DEFAULT_MOV_BASE = 2.2
DEFAULT_MOV_DIVISOR = 0.001


def _mov_multiplier(point_diff: int, winner_elo_diff: float, base: float, divisor: float) -> float:
    """Scales a rating update by how many points a game was decided by,
    log-dampened, and further dampened by winner_elo_diff (the winner's
    pre-game rating edge, home-field advantage included) so a big
    favorite winning big moves ratings less than a big underdog winning
    by the same margin.
    """
    return math.log(abs(point_diff) + 1) * (base / (winner_elo_diff * divisor + base))


def compute_elo_ratings(
    events: list[dict],
    k_factor: float = DEFAULT_K_FACTOR,
    home_advantage: float = DEFAULT_HOME_ADVANTAGE,
    starting_rating: float = DEFAULT_STARTING_RATING,
    mov_base: float = DEFAULT_MOV_BASE,
    mov_divisor: float = DEFAULT_MOV_DIVISOR,
) -> dict[str, dict[str, float]]:
    """Walks head-to-head events in chronological order, updating a running
    Elo-style rating per team. Returns each event's PRE-game ratings, keyed
    by event_key -- using the post-game rating would leak that event's own
    outcome into its own features.

    Margin-of-victory-scaled (see _mov_multiplier) -- a blowout moves
    ratings more than a one-score win. Ties count as a 0.5 result for both
    teams with no MOV scaling applied (there's no winner to measure a
    margin from). Events without both a home/away role or a final score
    still get a pre-game rating recorded, they just don't produce a
    rating update.
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

        point_diff = home_score - away_score
        if home_score > away_score:
            home_actual, away_actual = 1.0, 0.0
            winner_elo_diff = (home_rating + home_advantage) - away_rating
        elif home_score < away_score:
            home_actual, away_actual = 0.0, 1.0
            winner_elo_diff = away_rating - (home_rating + home_advantage)
        else:
            home_actual = away_actual = 0.5
            winner_elo_diff = 0.0

        mov_multiplier = (
            _mov_multiplier(point_diff, winner_elo_diff, mov_base, mov_divisor) if point_diff != 0 else 1.0
        )

        expected_home = 1 / (1 + 10 ** ((away_rating - (home_rating + home_advantage)) / 400))
        expected_away = 1 - expected_home

        ratings[home_id] = home_rating + k_factor * mov_multiplier * (home_actual - expected_home)
        ratings[away_id] = away_rating + k_factor * mov_multiplier * (away_actual - expected_away)

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


def current_streak(team_events: list[dict], entity_id: str) -> int:
    """team_events: a team's own completed events, most recent first, NOT
    including the event being scored. Positive = current win streak
    length, negative = current loss streak length, 0 if there's no
    history yet or the most recent game was a tie.

    Compares scores directly rather than reading each participant's
    `won` flag -- scoreboard_event_to_event_item sets `won=False` for
    BOTH sides on a tie, so the flag alone can't distinguish a tie from a
    loss the way a direct score comparison can.
    """
    streak = 0
    for event in team_events:
        participants = event.get("participants", [])
        own = next((p for p in participants if p.get("entity_id") == entity_id), None)
        opponent = next((p for p in participants if p.get("entity_id") != entity_id), None)
        if own is None or opponent is None:
            break
        own_score = own.get("result", {}).get("score")
        opp_score = opponent.get("result", {}).get("score")
        if own_score is None or opp_score is None or own_score == opp_score:
            break
        won = own_score > opp_score
        if streak == 0:
            streak = 1 if won else -1
        elif (streak > 0) == won:
            streak += 1 if won else -1
        else:
            break
    return streak


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


def _identify_leader(team_player_games: list[dict], volume_stat: str) -> dict | None:
    """Given one team's player_game_stats rows for a single event, returns
    whoever had the most of `volume_stat`. Returns the full
    player_game_stats row (not just an ID) so the caller can use it
    directly as one entry of that player's own rolling history, or None
    if nobody on the team recorded that stat."""
    candidates = [row for row in team_player_games if volume_stat in row.get("stat_line", {})]
    if not candidates:
        return None
    return max(candidates, key=lambda row: row["stat_line"][volume_stat])


def identify_starting_qb(team_player_games: list[dict]) -> dict | None:
    """The player with the most passing attempts -- see _identify_leader."""
    return _identify_leader(team_player_games, "passing_attempts")


def identify_lead_rusher(team_player_games: list[dict]) -> dict | None:
    """The player with the most rushing attempts -- see _identify_leader."""
    return _identify_leader(team_player_games, "rushing_attempts")


def identify_lead_receiver(team_player_games: list[dict]) -> dict | None:
    """The player with the most receiving targets (not receptions -- targets
    reflect who the offense is actually looking for, independent of how
    many of those looks were caught) -- see _identify_leader."""
    return _identify_leader(team_player_games, "receiving_targets")


def _rate(averages: dict, numerator_key: str, denominator_key: str) -> float | None:
    denominator = averages.get(denominator_key)
    if not denominator:
        return None
    return averages[numerator_key] / denominator


def build_event_features(
    event: dict,
    elo_ratings: dict[str, dict[str, float]],
    home_team_events: list[dict],
    away_team_events: list[dict],
    window: int = DEFAULT_ROLLING_WINDOW,
    home_qb_games: list[dict] | None = None,
    away_qb_games: list[dict] | None = None,
    home_rb_games: list[dict] | None = None,
    away_rb_games: list[dict] | None = None,
    home_wr_games: list[dict] | None = None,
    away_wr_games: list[dict] | None = None,
    home_team_box_stats: list[dict] | None = None,
    away_team_box_stats: list[dict] | None = None,
) -> dict:
    """Assembles one training row for a head-to-head event: game-outcome
    (win/loss) and game-score features and labels share this same row,
    since both targets are trained from the same inputs.

    elo_ratings is compute_elo_ratings' full output. home_team_events/
    away_team_events are each team's own prior completed events, most
    recent first, NOT including this one (see FeatureStorage.get_team_events
    with before_date=event['event_date']).

    home_qb_games/away_qb_games, home_rb_games/away_rb_games, and
    home_wr_games/away_wr_games are each event's identified key player's
    (see identify_starting_qb/identify_lead_rusher/identify_lead_receiver)
    own prior completed games, most recent first, NOT including this one
    -- the same shape rolling_player_stat_averages expects (see
    FeatureStorage.get_player_game_stats). None (or an empty list) when a
    leader couldn't be identified for that team/position, in which case
    those columns are just None.

    home_team_box_stats/away_team_box_stats are each team's own prior
    team_game_stats rows (see FeatureStorage.get_all_team_game_stats),
    most recent first, NOT including this one.
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

    home_qb_stats = rolling_player_stat_averages(home_qb_games or [], window)
    away_qb_stats = rolling_player_stat_averages(away_qb_games or [], window)
    home_rb_stats = rolling_player_stat_averages(home_rb_games or [], window)
    away_rb_stats = rolling_player_stat_averages(away_rb_games or [], window)
    home_wr_stats = rolling_player_stat_averages(home_wr_games or [], window)
    away_wr_stats = rolling_player_stat_averages(away_wr_games or [], window)

    # team_game_stats rows carry a stat_line the same shape
    # rolling_player_stat_averages already handles generically.
    home_box_stats = rolling_player_stat_averages(home_team_box_stats or [], window)
    away_box_stats = rolling_player_stat_averages(away_team_box_stats or [], window)
    home_third_down_pct = _rate(home_box_stats, "avg_third_down_conversions", "avg_third_down_attempts")
    away_third_down_pct = _rate(away_box_stats, "avg_third_down_conversions", "avg_third_down_attempts")
    home_red_zone_pct = _rate(home_box_stats, "avg_red_zone_conversions", "avg_red_zone_attempts")
    away_red_zone_pct = _rate(away_box_stats, "avg_red_zone_conversions", "avg_red_zone_attempts")

    home_win_streak = current_streak(home_team_events, home_id)
    away_win_streak = current_streak(away_team_events, away_id)

    venue_city = event.get("venue_city")
    # Every domestic US venue address has a state; international ones
    # never do (see library.features.nfl_teams). A venue with no state
    # AND no entry in INTERNATIONAL_VENUES means either a new
    # international host city needs adding there, or this is a data
    # quirk worth a look -- either way, travel_distances_km is silently
    # falling back to the ordinary-game assumption for this event.
    if venue_city and event.get("venue_state") is None and venue_city not in INTERNATIONAL_VENUES:
        logger.warning(
            "Event %s has venue_city=%r with no US state and no entry in "
            "INTERNATIONAL_VENUES -- travel distance for this game is "
            "likely wrong; consider adding it to nfl_teams.INTERNATIONAL_VENUES.",
            event.get("event_key"), venue_city,
        )
    home_travel_km, away_travel_km = travel_distances_km(away_id, home_id, venue_city)

    return {
        "event_key": event["event_key"],
        "event_date": event["event_date"],
        "home_entity_id": home_id,
        "away_entity_id": away_id,
        # Both already land in DynamoDB via scoreboard_event_to_event_item
        # (library/normalize/espn.py) -- no new data source needed, these
        # just weren't being surfaced as model inputs. Unlike event_date,
        # these two ARE meant to be real features: week captures how far
        # into the season a game falls, and season_type distinguishes
        # regular-season from postseason games, which plausibly behave
        # differently even at the same week number.
        "week": event.get("week"),
        "season_type": event.get("season_type"),
        # venue_indoor and weather_temperature are real feature inputs (a
        # dome neutralizes weather entirely, and cold/heat plausibly
        # affects passing/kicking games); venue_city/venue_state are
        # carried through for reference only -- raw strings aren't
        # model-consumable without encoding, so train_model.py excludes
        # them from its feature columns (see DATA_SCHEMA.md).
        "venue_indoor": event.get("venue_indoor"),
        "venue_city": event.get("venue_city"),
        "venue_state": event.get("venue_state"),
        "weather_temperature": event.get("weather_temperature"),
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
        # Keys match the stat_line field names normalize.py actually
        # produces: "passing_yards"/"passing_touchdowns" (clean, no
        # double-prefix), and "passing_interceptions" (prefixed, distinct
        # from the "interceptions" category's own bare "interceptions" key).
        "home_qb_avg_passing_yards": home_qb_stats.get("avg_passing_yards"),
        "home_qb_avg_passing_tds": home_qb_stats.get("avg_passing_touchdowns"),
        "home_qb_avg_interceptions": home_qb_stats.get("avg_passing_interceptions"),
        "home_qb_games_played": home_qb_stats["games_played"],
        "away_qb_avg_passing_yards": away_qb_stats.get("avg_passing_yards"),
        "away_qb_avg_passing_tds": away_qb_stats.get("avg_passing_touchdowns"),
        "away_qb_avg_interceptions": away_qb_stats.get("avg_passing_interceptions"),
        "away_qb_games_played": away_qb_stats["games_played"],
        # Lead rusher/receiver, same identify-then-track-their-own-history
        # approach as the QB above (see identify_lead_rusher/identify_lead_receiver).
        "home_rb_avg_rushing_yards": home_rb_stats.get("avg_rushing_yards"),
        "home_rb_avg_rushing_tds": home_rb_stats.get("avg_rushing_touchdowns"),
        "home_rb_games_played": home_rb_stats["games_played"],
        "away_rb_avg_rushing_yards": away_rb_stats.get("avg_rushing_yards"),
        "away_rb_avg_rushing_tds": away_rb_stats.get("avg_rushing_touchdowns"),
        "away_rb_games_played": away_rb_stats["games_played"],
        "home_wr_avg_receiving_yards": home_wr_stats.get("avg_receiving_yards"),
        "home_wr_avg_receiving_tds": home_wr_stats.get("avg_receiving_touchdowns"),
        "home_wr_avg_receptions": home_wr_stats.get("avg_receiving_receptions"),
        "home_wr_games_played": home_wr_stats["games_played"],
        "away_wr_avg_receiving_yards": away_wr_stats.get("avg_receiving_yards"),
        "away_wr_avg_receiving_tds": away_wr_stats.get("avg_receiving_touchdowns"),
        "away_wr_avg_receptions": away_wr_stats.get("avg_receiving_receptions"),
        "away_wr_games_played": away_wr_stats["games_played"],
        "home_avg_turnovers": home_box_stats.get("avg_turnovers"),
        "home_avg_total_yards": home_box_stats.get("avg_total_yards"),
        "home_avg_possession_time_seconds": home_box_stats.get("avg_possession_time_seconds"),
        "home_third_down_pct": home_third_down_pct,
        "home_red_zone_pct": home_red_zone_pct,
        "home_box_games_played": home_box_stats["games_played"],
        "away_avg_turnovers": away_box_stats.get("avg_turnovers"),
        "away_avg_total_yards": away_box_stats.get("avg_total_yards"),
        "away_avg_possession_time_seconds": away_box_stats.get("avg_possession_time_seconds"),
        "away_third_down_pct": away_third_down_pct,
        "away_red_zone_pct": away_red_zone_pct,
        "away_box_games_played": away_box_stats["games_played"],
        "is_divisional_game": is_divisional_game(home_id, away_id),
        "is_international_game": is_international_game(venue_city),
        "home_travel_km": home_travel_km,
        "away_travel_km": away_travel_km,
        "home_win_streak": home_win_streak,
        "away_win_streak": away_win_streak,
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
