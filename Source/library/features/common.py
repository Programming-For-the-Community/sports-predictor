"""
Sport-agnostic feature-computation functions, shared by every head-to-head
sport's adapter (see design/PROJECT_PLAN.md Phase 4's "extract shared
feature-engineering primitives" item). Extracted out of library.features.nfl,
which had no NFL-specific assumptions in these functions -- they operate
purely on the generic event/participant shape library.normalize.espn
produces (participants[].{entity_id,role,result.score}) and on the generic
stat_line dict shape player_game_stats/team_game_stats rows carry, with no
football-only field names. Kept separate from library.features.nfl so a
second sport's adapter can import these without also importing NFL-only
names (identify_starting_qb, build_event_features, etc.).
"""
import math
from datetime import date, datetime

DEFAULT_ROLLING_WINDOW = 5
DEFAULT_STARTING_RATING = 1500.0
DEFAULT_K_FACTOR = 20.0
DEFAULT_HOME_ADVANTAGE = 55.0
# Fraction of a team's rating DEVIATION from starting_rating that survives
# a season boundary -- see compute_elo_ratings' own docstring. 2/3 matches
# FiveThirtyEight's published NFL Elo methodology (revert ratings 1/3 of
# the way back to the league-average baseline between seasons).
DEFAULT_SEASON_CARRYOVER = 2 / 3
# FiveThirtyEight's NFL Elo margin-of-victory constant -- see _mov_multiplier.
# These are NFL-tuned defaults; a second sport's adapter should pass its own
# tuned overrides rather than inherit NFL's silently.
DEFAULT_MOV_BASE = 2.2
DEFAULT_MOV_DIVISOR = 0.001


def expected_score(rating: float, opponent_rating: float, rating_advantage: float = 0.0) -> float:
    """Standard Elo expected-score formula: probability `rating` (plus
    any home-field rating_advantage) beats opponent_rating. Extracted out
    of compute_elo_ratings so other callers needing the same win
    probability (e.g. aws-lambdas/nfl/predict/season_simulation.py's
    Monte Carlo season simulation) share the exact formula rather than
    risking a second copy drifting from what actually updates ratings."""
    return 1 / (1 + 10 ** ((opponent_rating - (rating + rating_advantage)) / 400))


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
    season_carryover: float = DEFAULT_SEASON_CARRYOVER,
    as_of_season: int | None = None,
) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    """Walks head-to-head events in chronological order, updating a running
    Elo-style rating per team. Returns (pre_game_ratings, current_ratings):
    pre_game_ratings is each event's PRE-game ratings, keyed by event_key --
    using the post-game rating would leak that event's own outcome into its
    own features, which is what every training-time caller uses.
    current_ratings is each team's rating after every event passed in has
    been processed, keyed by entity_id -- for a team with no upcoming event
    to look up a pre-game rating from yet, e.g. a live inference request
    for a game that hasn't happened.

    Margin-of-victory-scaled (see _mov_multiplier) -- a blowout moves
    ratings more than a one-score win. Ties count as a 0.5 result for both
    teams with no MOV scaling applied (there's no winner to measure a
    margin from). Events without both a home/away role or a final score
    still get a pre-game rating recorded, they just don't produce a
    rating update.

    Crossing a season boundary (detected from each event's own `season`
    field) regresses every existing rating toward starting_rating by
    season_carryover. Events with no `season` field never trigger this,
    so a caller that already scopes its own events to one season is
    unaffected.

    as_of_season applies one more trailing regression after the loop if
    the season it names differs from the last event actually processed --
    covers a caller whose `events` has no completed game yet in the new
    season for the loop to ever see.
    """
    ratings: dict[str, float] = {}
    pre_game_ratings: dict[str, dict[str, float]] = {}
    current_season = None

    def _regress(ratings: dict[str, float]) -> dict[str, float]:
        return {
            team_id: starting_rating + season_carryover * (rating - starting_rating)
            for team_id, rating in ratings.items()
        }

    for event in sorted(events, key=lambda e: e["event_date"]):
        event_season = event.get("season")
        if event_season is not None and current_season is not None and event_season != current_season:
            ratings = _regress(ratings)
        current_season = event_season

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

        expected_home = expected_score(home_rating, away_rating, home_advantage)
        expected_away = 1 - expected_home

        ratings[home_id] = home_rating + k_factor * mov_multiplier * (home_actual - expected_home)
        ratings[away_id] = away_rating + k_factor * mov_multiplier * (away_actual - expected_away)

    if as_of_season is not None and current_season is not None and as_of_season != current_season:
        ratings = _regress(ratings)

    return pre_game_ratings, ratings


def kickoff_hour_utc(kickoff_time: str | None) -> int | None:
    """UTC hour (0-23) parsed from an ISO 8601 kickoff timestamp -- both
    ESPN's and CFBD's own kickoff_time fields use this format. None for a
    missing or unparseable timestamp."""
    if not kickoff_time:
        return None
    try:
        return datetime.fromisoformat(kickoff_time.replace("Z", "+00:00")).hour
    except ValueError:
        return None


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
    # How many of the windowed games actually had this key, not just the
    # blanket games_played/starts below -- lets a caller distinguish a
    # real, established stat (e.g. a starting QB's passing_yards) from
    # one a player recorded once by fluke (see
    # train_player_prop_model.py's MIN_PRIOR_GAMES_WITH_STAT).
    averages.update({f"games_with_{key}": counts[key] for key in counts})
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


def _identify_top_leaders(team_player_games: list[dict], volume_stat: str, n: int) -> list[dict]:
    """Same shape as _identify_leader but returns the top n rows instead
    of just the single leader -- used where a live prediction needs
    several candidates per team (e.g. the top 3 receivers), not just one."""
    candidates = [row for row in team_player_games if volume_stat in row.get("stat_line", {})]
    return sorted(candidates, key=lambda row: row["stat_line"][volume_stat], reverse=True)[:n]


def rank_by_average_stat(histories: dict[str, list[dict]], stat: str, n: int) -> list[str]:
    """Given each candidate's own recent player_game_stats rows (most
    recent first, e.g. from FeatureStorage.get_player_game_stats), ranks
    by their average of `stat` over that window and returns the top n
    entity_ids.

    Unlike _identify_leader/_identify_top_leaders (which pick by volume
    WITHIN ONE GAME's roster), this ranks by each candidate's OWN rolling
    performance across their own history. Needed for defensive_sacks --
    sacks are rare, bursty events, so "who had the most sacks in the last
    game" is a much noisier signal than "who has the highest average"
    the way passing_attempts/receiving_targets/rushing_attempts reliably
    identify a starter within a single game.
    """
    averages = []
    for entity_id, games in histories.items():
        values = [game["stat_line"][stat] for game in games if stat in game.get("stat_line", {})]
        if values:
            averages.append((entity_id, sum(values) / len(values)))
    averages.sort(key=lambda pair: pair[1], reverse=True)
    return [entity_id for entity_id, _ in averages[:n]]


def _rate(averages: dict, numerator_key: str, denominator_key: str) -> float | None:
    denominator = averages.get(denominator_key)
    if not denominator:
        return None
    return averages[numerator_key] / denominator


# Ordinal, not one-hot -- these statuses form a real severity order (a
# tree model can split on "status >= Doubtful" directly), matching the
# severity threshold used for live leader-selection exclusion (Out AND
# Doubtful, not just Out -- see live_features.py). Any status
# string ESPN reports that isn't one of these three (rare -- IR/PUP-style
# season-ending designations mostly show up as "Out" already) falls back
# to 1, a conservative "something's reported" floor rather than silently
# treating an unrecognized status as healthy.
_INJURY_STATUS_ORDINAL = {"Questionable": 1, "Doubtful": 2, "Out": 3}
_TEAM_INJURY_COUNT_STATUSES = {"Doubtful", "Out"}


def _injury_status_ordinal(injuries: list[dict] | None, entity_id: str | None) -> int | None:
    """0=no report (checked, this player's healthy), 1=Questionable,
    2=Doubtful, 3=Out. None specifically means "never checked" (the event
    has no injuries data at all -- either older than this feature, or a
    fetch failure) -- distinct from 0, and left as None (not coerced to
    0) so it reaches training as a real missing value a tree model can
    treat as such, rather than a false "definitely healthy" signal for
    every pre-existing historical row."""
    if injuries is None or entity_id is None:
        return None
    for injury in injuries:
        if injury.get("entity_id") == entity_id:
            return _INJURY_STATUS_ORDINAL.get(injury.get("status"), 1)
    return 0


def _team_injury_count(injuries: list[dict] | None) -> int | None:
    """Count of players Doubtful or Out -- Questionable isn't counted
    here, same reasoning _injury_status_ordinal's severity order and the
    live leader-selection threshold both use: most Questionable players
    do play, so counting them would dilute this into a much noisier
    signal. None (not 0) when injuries data is entirely absent -- same
    missing-vs-zero distinction as _injury_status_ordinal."""
    if injuries is None:
        return None
    return sum(1 for injury in injuries if injury.get("status") in _TEAM_INJURY_COUNT_STATUSES)
