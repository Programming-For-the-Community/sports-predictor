"""
Builds the season projection (standings, National Ranking, conference-
tournament brackets, and the March Madness bracket) that Terraform/
scheduler-ncaambb-season-projection.tf's own scheduled EventBridge invoke
writes to S3 for GET /ncaambb/season. Team outcomes only -- no player-prop
leaderboard here, same established college-sport convention NCAAFB's own
season_projection.py already follows.

Pulls this season's data once via FeatureStorage, derives Elo ratings and
each team's own conference from a small S3 cache (raw_bucket's own
ncaambb/conference-membership/{season}.json, written daily by
schedule-sync/handler.py's own _sync_conference_membership) -- NOT a live
ESPN call made from here: this Lambda is VPC-attached with no route to
the public internet at all (no NAT Gateway; its security group only
opens 443 to the S3/DynamoDB VPC Gateway Endpoints' own prefix lists),
while schedule-sync is NOT VPC-attached and already reaches ESPN daily
for its own purposes -- see that module's own CONFERENCE MEMBERSHIP
docstring section for the full reasoning. A missing or stale cache
object degrades to "no known conference for anyone this run", same
"exclude, don't fabricate" treatment this project gives every other gap
like it, self-correcting the next time schedule-sync runs. Then runs
season_simulation's Monte Carlo logic with a ranking-model-backed
score_teams callable.

Unlike every other sport's single postseason bracket, NCAA MBB has TWO:
one bracket per conference tournament, each resolved independently, plus
the March Madness bracket that consumes each conference bracket's own
champion (real once known, projected until then) as that conference's
automatic bid -- see season_simulation.py's own module docstring. Both
get the same full real-vs-projected reconciliation NFL/NCAAFB/NBA's own
brackets already have (_resolve_matchup's 3-state design, copied in shape
from NCAAFB's own season_projection.py, unchanged -- it already operates
one round/one pair at a time and doesn't care how many rounds total there
are, or how many separate brackets call it).
"""
import logging
from datetime import date, datetime, timezone

import pandas as pd
from boto3.dynamodb.conditions import Key

import event_prediction
import model_loader
import season_simulation
from library.features.common import DEFAULT_HOME_ADVANTAGE, compute_elo_ratings, current_streak, average_opponent_elo, rolling_team_scoring_averages
from library.ml.model_types import ADAPTERS
from library.serving.common import enrich_bracket_team_names, enrich_team_standings
from library.serving.ncaambb_reads import WIN_PROBABILITY_MODEL, _actual_result, _home_and_away
from library.storage.feature_storage import FeatureStorage
from library.storage.season_projections import season_projection_key

logger = logging.getLogger("ncaambb-predict")

SPORT = "ncaambb"
RANKING_MODEL_NAME = "national-ranking"


def _is_regular_season_game(event: dict) -> bool:
    """True for a plain regular-season game (conference or non-
    conference) -- false for a conference-tournament game (same
    season_type as the regular season, but carries a tournament_note) and
    false for the NCAA tournament itself (season_type 3). Only regular-
    season games belong in simulate_season's own remaining_games list --
    both postseason tournaments are simulated separately, by
    season_simulation's own bracket walkers, not as more Elo games in the
    Monte Carlo's per-iteration schedule loop."""
    return event.get("season_type") == 2 and not event.get("tournament_note")


def _is_conference_tournament_game(event: dict) -> bool:
    """season_type 2 (same as the regular season) + conference_competition
    true + a real tournament_note headline -- a regular-season conference
    game has conference_competition true too, but an empty notes array
    (no tournament_note). Confirmed live, 2026-08-20 -- see
    project-ncaambb-onboarding memory."""
    return event.get("season_type") == 2 and bool(event.get("conference_competition")) and bool(event.get("tournament_note"))


def _is_march_madness_game(event: dict) -> bool:
    """season_type 3 + conference_competition false + a tournament_note
    headline starting with the NCAA tournament's own fixed headline
    prefix -- the NIT (and, unchecked, other secondary postseason
    tournaments) shares the exact same type/flag signature, so the notes
    headline itself is the only reliable signal. Confirmed live,
    2026-08-20 -- see project-ncaambb-onboarding memory."""
    return (
        event.get("season_type") == 3
        and not event.get("conference_competition")
        and (event.get("tournament_note") or "").startswith("Men's Basketball Championship - ")
    )


def _current_ncaambb_season(today: date) -> int:
    """Same calendar heuristic as schedule-sync/handler.py's own
    _current_ncaambb_season (August or later -> next calendar year,
    matching ESPN's own "the season is labeled by the year its second
    half falls in" convention) -- duplicated rather than imported since
    schedule-sync and this Lambda are separately deployed packages, same
    as every other cross-Lambda-in-one-sport duplication in this project.
    Not authoritative; _load_cached_team_conference below already
    degrades gracefully if this ever points past what schedule-sync's own
    cache has actually caught up to."""
    return today.year + 1 if today.month >= 8 else today.year


def _conference_membership_cache_key(season: int) -> str:
    return f"ncaambb/conference-membership/{season}.json"


def _load_cached_team_conference(raw_bucket, current_season: int | None) -> dict[str, str]:
    """Reads schedule-sync/handler.py's own daily-refreshed conference-
    membership cache from the raw bucket -- see this module's own
    docstring for why that Lambda resolves it, not this one. Missing
    object (a brand-new season the cache hasn't caught up to yet, or a
    transient write failure) degrades to "no known conference for
    anyone", same "exclude, don't fabricate" treatment as every other gap
    like it in this project."""
    if current_season is None:
        return {}
    key = _conference_membership_cache_key(current_season)
    try:
        if not raw_bucket.object_exists(key):
            logger.warning("No cached conference membership for season %s at %s yet -- standings will omit conference/bracket data this run", current_season, key)
            return {}
        return raw_bucket.get_json(key)["team_conference"]
    except Exception:
        logger.exception("Failed reading cached NCAA MBB conference membership for season %s -- standings will omit conference/bracket data this run", current_season)
        return {}


def _season_standings_inputs(storage: FeatureStorage, raw_bucket) -> dict:
    """Fetches this season's completed+scheduled events once and derives
    everything simulate_season and the ranking feature rows need.
    team_conference comes from schedule-sync's own daily S3 cache (see
    _load_cached_team_conference), not from event fields -- see this
    module's own docstring. remaining_games only keeps plain regular-
    season pairings (_is_regular_season_game) where both sides have a
    known conference."""
    scheduled = storage.get_all_events(SPORT, status="scheduled")
    all_completed = storage.get_all_events(SPORT, status="completed")
    # A fixed calendar heuristic, not derived from event data -- the same
    # one schedule-sync/handler.py's own _current_ncaambb_season uses to
    # key its daily conference-membership cache, so this Lambda's own
    # season resolution can never drift out of sync with which cache
    # object _load_cached_team_conference below actually reads. Forward-
    # looking by design: during the off-season this correctly points at
    # the upcoming season (0 real games yet, a live Monte Carlo projection
    # off the model's own priors + whatever of the new season is already
    # scheduled) rather than freezing on the season that already finished.
    current_season = _current_ncaambb_season(datetime.now(timezone.utc).date())
    scheduled = [e for e in scheduled if e.get("season") == current_season]
    completed = [e for e in all_completed if e.get("season") == current_season]

    team_conference = _load_cached_team_conference(raw_bucket, current_season)

    wins: dict[str, int] = {}
    losses: dict[str, int] = {}
    point_differential: dict[str, int] = {}
    team_last_completed_date: dict[str, str] = {}
    conference_wins: dict[str, int] = {}
    conference_losses: dict[str, int] = {}
    completed_by_team: dict[str, list[dict]] = {}
    for event in completed:
        home_away = _home_and_away(event)
        if home_away is None:
            continue
        home_id, away_id = home_away
        completed_by_team.setdefault(home_id, []).append(event)
        completed_by_team.setdefault(away_id, []).append(event)
        is_conference = bool(event.get("conference_competition"))

        for entity_id, opponent_id in (home_away, home_away[::-1]):
            participant = next(p for p in event["participants"] if p.get("entity_id") == entity_id)
            opponent = next(p for p in event["participants"] if p.get("entity_id") == opponent_id)
            score = (participant.get("result") or {}).get("score")
            opponent_score = (opponent.get("result") or {}).get("score")
            if score is None or opponent_score is None:
                continue
            won = score > opponent_score
            wins[entity_id] = wins.get(entity_id, 0) + (1 if won else 0)
            losses[entity_id] = losses.get(entity_id, 0) + (0 if won else 1)
            if is_conference:
                conference_wins[entity_id] = conference_wins.get(entity_id, 0) + (1 if won else 0)
                conference_losses[entity_id] = conference_losses.get(entity_id, 0) + (0 if won else 1)
            point_differential[entity_id] = point_differential.get(entity_id, 0) + (score - opponent_score)
            event_date = event.get("event_date", "")
            if event_date > team_last_completed_date.get(entity_id, ""):
                team_last_completed_date[entity_id] = event_date

    for team_id, team_events in completed_by_team.items():
        team_events.sort(key=lambda e: e.get("event_date", ""), reverse=True)

    pre_game_ratings, current_ratings = compute_elo_ratings(all_completed, as_of_season=current_season)

    avg_points_scored: dict[str, float | None] = {}
    avg_points_allowed: dict[str, float | None] = {}
    win_streak: dict[str, int] = {}
    strength_of_schedule: dict[str, float | None] = {}
    for team_id, team_events in completed_by_team.items():
        scoring = rolling_team_scoring_averages(team_events, team_id, window=len(team_events))
        avg_points_scored[team_id] = scoring["avg_points_scored"]
        avg_points_allowed[team_id] = scoring["avg_points_allowed"]
        win_streak[team_id] = current_streak(team_events, team_id)
        strength_of_schedule[team_id] = average_opponent_elo(team_events, team_id, pre_game_ratings)

    scheduled_sorted = sorted(scheduled, key=lambda e: e.get("event_date", ""))
    remaining_games: list[tuple[str, str, bool]] = []
    team_next_event: dict[str, str] = {}
    for event in scheduled_sorted:
        home_away = _home_and_away(event)
        if home_away is None:
            continue
        home_id, away_id = home_away
        if _is_regular_season_game(event) and home_id in team_conference and away_id in team_conference:
            remaining_games.append((home_id, away_id, bool(event.get("conference_competition"))))
        team_next_event.setdefault(home_id, event["event_key"])
        team_next_event.setdefault(away_id, event["event_key"])

    return {
        "current_season": current_season,
        "wins": wins,
        "losses": losses,
        "conference_wins": conference_wins,
        "conference_losses": conference_losses,
        "point_differential": point_differential,
        "current_ratings": current_ratings,
        "team_conference": team_conference,
        "remaining_games": remaining_games,
        "team_next_event": team_next_event,
        "team_last_completed_date": team_last_completed_date,
        "avg_points_scored": avg_points_scored,
        "avg_points_allowed": avg_points_allowed,
        "win_streak": win_streak,
        "strength_of_schedule": strength_of_schedule,
        "scheduled": scheduled,
        "completed": completed,
    }


def _ranking_feature_row(team_id: str, wins: dict, losses: dict, ratings: dict, season_inputs: dict) -> dict:
    """One team-poll row for the ranking model, matching
    build_team_week_features' column set (no "week"/"season" column --
    NCAA MBB's national-ranking model is poll-centric, not week-centric,
    see that function's own docstring; training_common.feature_columns
    already excludes both). wins/losses/elo are this Monte Carlo
    iteration's simulated values; avg_points_scored/allowed, win_streak,
    and strength_of_schedule stay at today's real season-to-date value
    (simulate_season never generates real scores to derive them from)."""
    games_played = wins.get(team_id, 0) + losses.get(team_id, 0)
    return {
        "elo": ratings.get(team_id),
        "wins": wins.get(team_id, 0),
        "losses": losses.get(team_id, 0),
        "games_played": games_played,
        "avg_points_scored": season_inputs["avg_points_scored"].get(team_id),
        "avg_points_allowed": season_inputs["avg_points_allowed"].get(team_id),
        "win_streak": season_inputs["win_streak"].get(team_id, 0),
        "strength_of_schedule": season_inputs["strength_of_schedule"].get(team_id),
    }


def _batch_score_teams(estimator, model_card: dict, teams: list[str], season_inputs: dict, wins: dict, losses: dict, ratings: dict) -> dict[str, float]:
    """Scores every team in one batched adapter.predict call (not one call per team).
    Bypasses model_loader.predict's single-row wrapper; same NaN-for-missing coercion, vectorized."""
    feature_columns = model_card["feature_columns"]
    rows = []
    for team_id in teams:
        row = _ranking_feature_row(team_id, wins, losses, ratings, season_inputs)
        rows.append({
            column: float(row[column]) if isinstance(row.get(column), (int, float)) else float("nan")
            for column in feature_columns
        })
    X = pd.DataFrame(rows, columns=feature_columns, index=teams)
    adapter = ADAPTERS[model_card["algorithm"]]
    predictions = adapter.predict(estimator, X)
    return dict(zip(teams, (float(value) for value in predictions)))


def _current_model_scores(estimator, model_card: dict, teams: list[str], season_inputs: dict) -> dict[str, float]:
    """Today's actual (not simulated) ranking-model score per team -- the
    SAME model/feature row simulate_season's own score_teams callable
    uses to pick each simulated season's March Madness field, scored once
    against real current wins/losses/ratings instead of a simulated
    future. Lower is better. Used by _current_rankings (standings'
    current_rank column) and by both real bracket payloads (seeding)."""
    return _batch_score_teams(
        estimator, model_card, teams, season_inputs,
        season_inputs["wins"], season_inputs["losses"], season_inputs["current_ratings"],
    )


def _current_rankings(estimator, model_card: dict, teams: list[str], season_inputs: dict) -> dict[str, int]:
    scores = _current_model_scores(estimator, model_card, teams, season_inputs)
    ranked = sorted(teams, key=lambda team_id: scores[team_id])
    return {team_id: rank for rank, team_id in enumerate(ranked, start=1)}


def _real_postseason_matchups(storage: FeatureStorage, current_season: int | None, predicate) -> dict[frozenset, dict]:
    """{frozenset({home_id, away_id}): event} for every real postseason
    game (conference tournament or March Madness, selected by `predicate`)
    this season, scheduled or completed."""
    result: dict[frozenset, dict] = {}
    for status in ("scheduled", "completed"):
        for event in storage.get_all_events(SPORT, status=status):
            if event.get("season") != current_season or not predicate(event):
                continue
            home_away = _home_and_away(event)
            if home_away is None:
                continue
            result[frozenset(home_away)] = event
    return result


def _logged_win_probability(predictions_table, event_key_value: str) -> dict | None:
    rows = predictions_table.query(Key("event_key").eq(event_key_value))
    row = next((r for r in rows if r["model_key"].startswith(f"MODEL#{WIN_PROBABILITY_MODEL}#")), None)
    return row["predicted_value"] if row else None


def _resolve_matchup(
    team_a: str, team_b: str | None, seed_a: int | None, seed_b: int | None,
    real_matchups: dict[frozenset, dict], storage: FeatureStorage, s3, predictions_table,
    current_ratings: dict[str, float], home_advantage: float,
) -> dict:
    """Resolves one bracket slot -- a 3-state design: (1) no real game
    exists yet -- the model's own deterministic pick ("status":
    "projected"); (2) a real game exists and is completed -- the actual
    result plus whatever was originally predicted, if anyone ever
    requested one ("status": "final"); (3) a real game exists, not yet
    played -- computed on the spot right here if nobody's viewed it yet
    ("status": "scheduled"). Copied in shape, unchanged, from NCAAFB's
    own _resolve_matchup -- see this module's own docstring.

    A bye (team_b is None -- only possible in a conference bracket, March
    Madness never has one after First Four) always resolves as
    "projected": there's no real game to look up."""
    if team_b is None:
        return {
            "status": "projected", "team_a": team_a, "seed_a": seed_a, "team_b": None, "seed_b": None,
            "predicted_winner": team_a, "win_probability": 1.0,
        }

    real_event = real_matchups.get(frozenset((team_a, team_b)))
    if real_event is None:
        matchup = season_simulation.project_matchup(team_a, team_b, seed_a, seed_b, current_ratings, home_advantage)
        matchup["status"] = "projected"
        return matchup

    event_key_value = real_event["event_key"]
    home_id, away_id = _home_and_away(real_event)

    if real_event.get("status") == "completed":
        actual = _actual_result(real_event)
        logged = _logged_win_probability(predictions_table, event_key_value)
        predicted_winner = win_probability = None
        if logged is not None:
            probability = logged["home_win_probability"]
            predicted_winner = home_id if probability >= 0.5 else away_id
            win_probability = probability if predicted_winner == home_id else 1 - probability
        return {
            "status": "final",
            "team_a": home_id, "team_b": away_id, "seed_a": seed_a, "seed_b": seed_b,
            "predicted_winner": predicted_winner, "win_probability": win_probability,
            "actual_winner": home_id if actual["home_won"] else away_id,
            "actual_home_score": actual["home_score"], "actual_away_score": actual["away_score"],
        }

    logged = _logged_win_probability(predictions_table, event_key_value)
    if logged is None:
        try:
            event_prediction.compute_and_cache_event(storage, s3, predictions_table, real_event["event_id"])
            logged = _logged_win_probability(predictions_table, event_key_value)
        except Exception:
            logger.exception("Failed computing a live prediction for bracket game %s", event_key_value)

    predicted_winner = win_probability = None
    if logged is not None:
        probability = logged["home_win_probability"]
        predicted_winner = home_id if probability >= 0.5 else away_id
        win_probability = probability if predicted_winner == home_id else 1 - probability

    return {
        "status": "scheduled",
        "team_a": home_id, "team_b": away_id, "seed_a": seed_a, "seed_b": seed_b,
        "predicted_winner": predicted_winner, "win_probability": win_probability,
    }


def _project_bracket_round(
    round_name: str, pairs: list[tuple[str, str | None, int | None, int | None]],
    real_matchups: dict[frozenset, dict], storage: FeatureStorage, s3, predictions_table,
    current_ratings: dict[str, float], home_advantage: float,
) -> tuple[dict, list[tuple[str, int | None]]]:
    matchups = []
    advancing = []
    for team_a, team_b, seed_a, seed_b in pairs:
        matchup = _resolve_matchup(
            team_a, team_b, seed_a, seed_b, real_matchups, storage, s3, predictions_table,
            current_ratings, home_advantage,
        )
        matchups.append(matchup)
        winner = matchup["predicted_winner"] if matchup["status"] != "final" else matchup["actual_winner"]
        winner_seed = seed_a if winner == team_a else seed_b
        advancing.append((winner, winner_seed))
    return {"round": round_name, "matchups": matchups}, advancing


def _reconcile_single_elim_bracket(
    seeded_teams: list[str], round_names: list[str], real_matchups: dict[frozenset, dict],
    storage: FeatureStorage, s3, predictions_table, current_ratings: dict[str, float], home_advantage: float,
) -> dict:
    """Walks the SAME seed-line/bye topology project_single_elim_bracket
    resolves deterministically, but through _resolve_matchup/
    _project_bracket_round's real-vs-projected reconciliation each round,
    instead of always picking the model's own deterministic winner. Used
    for both a conference bracket and (per region, then glued together by
    the caller) March Madness."""
    bracket_size = season_simulation._next_power_of_two(len(seeded_teams))
    seed_line = season_simulation._standard_seed_line(bracket_size)
    slot_team: dict[int, str | None] = {
        seed: (seeded_teams[seed - 1] if seed <= len(seeded_teams) else None) for seed in seed_line
    }

    rounds = []
    current_seeds = seed_line
    advantage = home_advantage
    for round_name in round_names:
        pairs = [
            (slot_team[current_seeds[i]], slot_team[current_seeds[i + 1]], current_seeds[i], current_seeds[i + 1])
            for i in range(0, len(current_seeds), 2)
        ]
        round_payload, advancing = _project_bracket_round(
            round_name, pairs, real_matchups, storage, s3, predictions_table, current_ratings, advantage,
        )
        rounds.append(round_payload)
        for winner, winner_seed in advancing:
            slot_team[winner_seed] = winner
        current_seeds = [seed for _, seed in advancing]
        advantage = 0.0

    return {"rounds": rounds, "champion": rounds[-1]["matchups"][0]["predicted_winner"] if rounds[-1]["matchups"][0]["status"] != "final" else rounds[-1]["matchups"][0]["actual_winner"]}


def _conference_bracket_payloads(
    storage: FeatureStorage, s3, predictions_table, season_inputs: dict, current_season: int | None,
) -> list[dict]:
    """One reconciled bracket per conference with at least 2 tracked
    members -- {"conference": name, "bracket": {"rounds": [...],
    "champion": team_id}}. Seeding is conference-only record (wins then
    point differential, see season_simulation._conference_seed_order's
    own docstring for the simplification this accepts)."""
    conferences = season_simulation._group_by_conference(season_inputs["team_conference"])
    real_matchups = _real_postseason_matchups(storage, current_season, _is_conference_tournament_game)
    ratings = season_inputs["current_ratings"]

    payloads = []
    for conference, members in sorted(conferences.items()):
        if len(members) < 2:
            continue
        seed_order = season_simulation._conference_seed_order(
            members, season_inputs["conference_wins"], season_inputs["conference_losses"], season_inputs["point_differential"],
        )
        round_names = season_simulation._round_names(season_simulation._next_power_of_two(len(seed_order)))
        try:
            bracket = _reconcile_single_elim_bracket(
                seed_order, round_names, real_matchups, storage, s3, predictions_table, ratings, DEFAULT_HOME_ADVANTAGE,
            )
        except Exception:
            logger.exception("Failed building conference bracket for %s -- skipping this run", conference)
            continue
        payloads.append({"conference": conference, "bracket": enrich_bracket_team_names(storage, SPORT, bracket)})
    return payloads


def _march_madness_bracket_payload(
    storage: FeatureStorage, s3, predictions_table, season_inputs: dict, current_season: int | None,
    estimator, model_card: dict, teams: list[str], conference_champions: dict[str, str],
) -> dict | None:
    """Reconciled March Madness bracket -- First Four seeded/resolved
    deterministically (no real First-Four results feed back in yet; a
    future improvement, not core to this build), then each of the 4
    regions walked through the same real-vs-projected reconciliation as
    a conference bracket. Unlike season_simulation.project_march_madness_
    bracket's own deterministic version (used only inside the Monte Carlo
    loop, never rendered), each region's own round list is kept separate
    here -- {"first_four", "regions": {name: {"rounds", "champion"}},
    "final_four", "championship", "champion"} -- so the frontend can draw
    the traditional 4-region tournament layout instead of one flat list.
    None if fewer than 2 conferences produced a champion (nothing to seed
    a field from)."""
    if len(conference_champions) < 2:
        return None

    model_scores = _current_model_scores(estimator, model_card, teams, season_inputs)
    auto_bids, at_large = season_simulation.select_march_madness_field(model_scores, conference_champions)
    ratings = season_inputs["current_ratings"]

    first_four = season_simulation.project_first_four(auto_bids, at_large, ratings, model_scores)
    regions = season_simulation._assign_regions(first_four["field"])
    real_matchups = _real_postseason_matchups(storage, current_season, _is_march_madness_game)

    region_brackets = {}
    for region_name, region_teams in regions.items():
        round_names = season_simulation.MARCH_MADNESS_REGION_ROUND_NAMES[: season_simulation._next_power_of_two(len(region_teams)).bit_length() - 1]
        try:
            region_brackets[region_name] = _reconcile_single_elim_bracket(
                region_teams, round_names, real_matchups, storage, s3, predictions_table, ratings, 0.0,
            )
        except Exception:
            logger.exception("Failed building March Madness region bracket for %s -- skipping this run", region_name)
            return None

    region_champions = [region_brackets[name]["champion"] for name in season_simulation.REGION_NAMES]
    final_four_round, final_four_advancing = _project_bracket_round(
        "Final Four",
        [(region_champions[0], region_champions[1], None, None), (region_champions[2], region_champions[3], None, None)],
        real_matchups, storage, s3, predictions_table, ratings, 0.0,
    )

    championship_round, championship_advancing = _project_bracket_round(
        "Championship",
        [(final_four_advancing[0][0], final_four_advancing[1][0], None, None)],
        real_matchups, storage, s3, predictions_table, ratings, 0.0,
    )

    bracket = {
        "first_four": first_four["matchups"],
        "regions": {
            region_name: {"rounds": region_brackets[region_name]["rounds"], "champion": region_brackets[region_name]["champion"]}
            for region_name in season_simulation.REGION_NAMES
        },
        "final_four": final_four_round["matchups"],
        "championship": championship_round["matchups"][0],
        "champion": championship_advancing[0][0],
    }
    return enrich_bracket_team_names(storage, SPORT, bracket)


def build_season_projection(storage: FeatureStorage, s3, predictions_table, raw_bucket) -> dict:
    season_inputs = _season_standings_inputs(storage, raw_bucket)
    teams = list(season_inputs["team_conference"])
    current_season = season_inputs["current_season"]

    simulation: dict[str, dict] = {}
    current_rankings: dict[str, int] = {}
    conference_brackets: list[dict] = []
    march_madness_bracket = None

    if teams:
        try:
            estimator, model_card = model_loader.load_current_model(s3, SPORT, RANKING_MODEL_NAME)
        except model_loader.NoPromotedModelError:
            logger.warning("No promoted %s model -- season simulation, rankings, and both brackets skipped this run", RANKING_MODEL_NAME)
            estimator = model_card = None

        if estimator is not None:
            def score_teams(wins: dict, losses: dict, ratings: dict) -> dict[str, float]:
                return _batch_score_teams(estimator, model_card, teams, season_inputs, wins, losses, ratings)

            try:
                simulation = season_simulation.simulate_season(
                    season_inputs["wins"], season_inputs["losses"],
                    season_inputs["conference_wins"], season_inputs["conference_losses"],
                    season_inputs["point_differential"], season_inputs["remaining_games"],
                    season_inputs["current_ratings"], season_inputs["team_conference"], score_teams,
                )
            except Exception:
                logger.exception("Failed running the Monte Carlo season simulation -- standings will omit projected/probability columns this run")

            try:
                current_rankings = _current_rankings(estimator, model_card, teams, season_inputs)
            except Exception:
                logger.exception("Failed to compute current_rank -- standings will omit it this run")

            try:
                conference_brackets = _conference_bracket_payloads(storage, s3, predictions_table, season_inputs, current_season)
            except Exception:
                logger.exception("Failed building conference brackets")

            conference_champions = {payload["conference"]: payload["bracket"]["champion"] for payload in conference_brackets}
            try:
                march_madness_bracket = _march_madness_bracket_payload(
                    storage, s3, predictions_table, season_inputs, current_season,
                    estimator, model_card, teams, conference_champions,
                )
            except Exception:
                logger.exception("Failed building the March Madness bracket")

    standings = sorted(
        (
            {
                "team_id": team_id,
                "conference": season_inputs["team_conference"].get(team_id),
                "wins": season_inputs["wins"].get(team_id, 0),
                "losses": season_inputs["losses"].get(team_id, 0),
                "current_rank": current_rankings.get(team_id),
                **simulation.get(team_id, {}),
            }
            for team_id in teams
        ),
        key=lambda row: row.get("projected_wins", row["wins"]),
        reverse=True,
    )
    standings = enrich_team_standings(storage, SPORT, standings)

    return {
        "sport": SPORT,
        "season": current_season,
        "standings": standings,
        "conference_brackets": conference_brackets,
        "march_madness_bracket": march_madness_bracket,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def run_scheduled(storage: FeatureStorage, model_bucket, predictions_table, raw_bucket) -> dict:
    """Entry point for the scheduled EventBridge invoke -- computes the
    projection once and writes it to S3. predictions_table is used by
    both bracket payloads to read/write real postseason games' logged
    predictions. raw_bucket is read-only here, scoped to the ncaambb/
    conference-membership/* prefix only (see iam-lambda-inference.tf)."""
    result = build_season_projection(storage, model_bucket, predictions_table, raw_bucket)
    model_bucket.put_json(season_projection_key(SPORT), result)
    logger.info("Wrote season projection for %s to S3", SPORT)
    return {"status": "ok"}
