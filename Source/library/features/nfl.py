"""
Pure NFL feature-computation functions, shared between the batch Fargate
feature-engineering task (building a training dataset from full history)
and the inference Lambda (building a live feature vector for one upcoming
matchup). No AWS calls live here; every function takes already-fetched
rows (from FeatureStorage) and returns numbers. Keeping train-time and
serve-time feature computation as the same functions prevents train/serve
skew.

Not covered here: season win totals, Super Bowl winner, and playoff game
winners. Those are produced by simulating a season/bracket using the
per-game outcome model's win probabilities repeatedly, built on top of
the event-level features below.
"""
import logging

from library.features.common import (
    DEFAULT_ROLLING_WINDOW,
    _identify_leader,
    _identify_top_leaders,
    _injury_status_ordinal,
    _rate,
    _team_injury_count,
    compute_elo_ratings,
    current_streak,
    kickoff_hour_utc,
    rest_days,
    rolling_player_stat_averages,
    rolling_team_scoring_averages,
)
from library.features.nfl_teams import INTERNATIONAL_VENUES, is_divisional_game, is_international_game, travel_distances_km

logger = logging.getLogger(__name__)


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


def identify_top_receivers(team_player_games: list[dict], n: int = 3) -> list[dict]:
    """The top n players by receiving targets -- see identify_lead_receiver
    for why targets, not receptions."""
    return _identify_top_leaders(team_player_games, "receiving_targets", n)


def identify_top_rushers(team_player_games: list[dict], n: int = 2) -> list[dict]:
    """The top n players by rushing attempts -- see identify_lead_rusher."""
    return _identify_top_leaders(team_player_games, "rushing_attempts", n)


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

    # Injury status is scoped to the presumptive QB specifically. home_qb_games/
    # away_qb_games' rows all belong to the same identified player, so the
    # first row's entity_id is that player's id.
    home_qb_entity_id = home_qb_games[0].get("entity_id") if home_qb_games else None
    away_qb_entity_id = away_qb_games[0].get("entity_id") if away_qb_games else None
    home_injuries = event.get("home_injuries")
    away_injuries = event.get("away_injuries")

    venue_city = event.get("venue_city")
    # Every domestic US venue address has a state; international ones
    # never do. A venue with no state and no entry in INTERNATIONAL_VENUES
    # means travel_distances_km is silently falling back to the
    # ordinary-game assumption for this event.
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
        # week captures how far into the season a game falls; season_type
        # distinguishes regular-season from postseason games.
        "week": event.get("week"),
        "season_type": event.get("season_type"),
        "kickoff_hour_utc": kickoff_hour_utc(event.get("kickoff_time")),
        # venue_indoor and weather_temperature are real feature inputs (a
        # dome neutralizes weather entirely); venue_city/venue_state are
        # carried through for reference only -- raw strings aren't
        # model-consumable without encoding.
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
        "home_avg_penalties": home_box_stats.get("avg_penalties"),
        "home_avg_penalty_yards": home_box_stats.get("avg_penalty_yards"),
        "home_third_down_pct": home_third_down_pct,
        "home_red_zone_pct": home_red_zone_pct,
        "home_box_games_played": home_box_stats["games_played"],
        "away_avg_turnovers": away_box_stats.get("avg_turnovers"),
        "away_avg_total_yards": away_box_stats.get("avg_total_yards"),
        "away_avg_possession_time_seconds": away_box_stats.get("avg_possession_time_seconds"),
        "away_avg_penalties": away_box_stats.get("avg_penalties"),
        "away_avg_penalty_yards": away_box_stats.get("avg_penalty_yards"),
        "away_third_down_pct": away_third_down_pct,
        "away_red_zone_pct": away_red_zone_pct,
        "away_box_games_played": away_box_stats["games_played"],
        "is_divisional_game": is_divisional_game(home_id, away_id),
        "is_international_game": is_international_game(venue_city),
        "home_travel_km": home_travel_km,
        "away_travel_km": away_travel_km,
        "home_win_streak": home_win_streak,
        "away_win_streak": away_win_streak,
        # Coach/injury fields are absent (None) if ingest enrichment
        # didn't run. Raw coach identity is never a feature here, only
        # experience/season_win_pct/career_playoff_win_pct, used directly
        # with no derivation. season_win_pct and career_playoff_win_pct
        # are both included: season_win_pct is a full, stable
        # regular-season sample but says nothing about big-game
        # performance, which career_playoff_win_pct captures.
        "home_coach_experience": event.get("home_coach_experience"),
        "away_coach_experience": event.get("away_coach_experience"),
        "home_coach_season_win_pct": event.get("home_coach_season_win_pct"),
        "away_coach_season_win_pct": event.get("away_coach_season_win_pct"),
        "home_coach_career_playoff_win_pct": event.get("home_coach_career_playoff_win_pct"),
        "away_coach_career_playoff_win_pct": event.get("away_coach_career_playoff_win_pct"),
        "home_qb_injury_status": _injury_status_ordinal(home_injuries, home_qb_entity_id),
        "away_qb_injury_status": _injury_status_ordinal(away_injuries, away_qb_entity_id),
        "home_team_injury_count": _team_injury_count(home_injuries),
        "away_team_injury_count": _team_injury_count(away_injuries),
        # Labels -- the training targets (win/loss and final score), not
        # model inputs. None when this is called to build a live feature
        # vector for a not-yet-played event.
        "label_home_won": home.get("result", {}).get("won"),
        "label_home_score": home.get("result", {}).get("score"),
        "label_away_score": away.get("result", {}).get("score"),
    }


def build_player_features(
    player_game: dict,
    prior_games: list[dict],
    event: dict,
    elo_ratings: dict[str, dict[str, float]],
    own_previous_event_date: str | None,
    window: int = DEFAULT_ROLLING_WINDOW,
) -> dict:
    """Assembles one training row for a player-prop target: player_game is
    the specific game being labeled (its stat_line becomes the training
    label); prior_games is that player's own completed games before this
    one, most recent first (see FeatureStorage.get_player_game_stats with
    before_date=player_game['event_date']).

    event is the full event record player_game belongs to (see
    FeatureStorage.get_all_events), elo_ratings is compute_elo_ratings'
    full output, and own_previous_event_date is this player's own team's
    (not the player's own) most recent prior event date, for rest_days --
    reoriented to one player's perspective (own/opponent rather than
    home/away) since a player-prop row has no "home team" of its own."""
    participants = event["participants"]
    home = next(p for p in participants if p.get("role") == "home")
    away = next(p for p in participants if p.get("role") == "away")
    home_id, away_id = home["entity_id"], away["entity_id"]
    team_id = player_game["team_id"]
    is_home = team_id == home_id
    opponent_id = away_id if is_home else home_id

    ratings = elo_ratings.get(event["event_key"], {})
    home_elo = ratings.get("home_pre_rating")
    away_elo = ratings.get("away_pre_rating")
    own_elo = home_elo if is_home else away_elo
    opponent_elo = away_elo if is_home else home_elo

    # No unrecognized-venue warning here -- build_event_dataset already
    # logs it once per event; repeating it per player would spam once per
    # roster spot.
    venue_city = event.get("venue_city")
    home_travel_km, away_travel_km = travel_distances_km(away_id, home_id, venue_city)
    own_travel_km = home_travel_km if is_home else away_travel_km

    averages = rolling_player_stat_averages(prior_games, window)
    return {
        "event_key": player_game["event_key"],
        "player_key": player_game["player_key"],
        "entity_id": player_game["entity_id"],
        "team_id": team_id,
        "opponent_id": opponent_id,
        "event_date": player_game["event_date"],
        **averages,
        "is_home": is_home,
        "week": event.get("week"),
        "season_type": event.get("season_type"),
        "kickoff_hour_utc": kickoff_hour_utc(event.get("kickoff_time")),
        "venue_indoor": event.get("venue_indoor"),
        "weather_temperature": event.get("weather_temperature"),
        "rest_days": rest_days(player_game["event_date"], own_previous_event_date),
        "own_elo": own_elo,
        "opponent_elo": opponent_elo,
        "elo_diff": (own_elo - opponent_elo) if own_elo is not None and opponent_elo is not None else None,
        "is_divisional_game": is_divisional_game(home_id, away_id),
        "is_international_game": is_international_game(venue_city),
        "travel_km": own_travel_km,
        # Label -- this game's actual stat line, the training target.
        "label_stat_line": player_game.get("stat_line", {}),
        "label_started": player_game.get("started"),
    }
