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
automatic bid -- see season_simulation.py's own module docstring. Each is
shown as one simulated run from the real games so far, like every other
sport's bracket (library.serving.bracket_simulation).
"""
import logging
import re
from datetime import date, datetime, timezone

import event_prediction
import season_simulation
from library.features.common import DEFAULT_HOME_ADVANTAGE, compute_elo_ratings
from library.http.ncaambb_core import ap_poll_to_rank_by_team
from library.serving import bracket_projection
from library.serving.bracket_simulation import BracketSimulation, RealPostseason, elo_rules
from library.serving import model_loader, season_projection_common
from library.serving.common import enrich_bracket_team_names, enrich_team_standings
from library.serving.ncaambb_reads import _home_and_away
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


def _current_season_events(storage: FeatureStorage) -> tuple[list[dict], list[dict], list[dict], int]:
    """(scheduled, all_completed, completed, current_season) -- scheduled/
    completed are scoped to just current_season, all_completed is the full
    (unscoped) history compute_elo_ratings needs for its own season-
    boundary regression."""
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
    return scheduled, all_completed, completed, current_season


def _record_game_result(
    event: dict, entity_id: str, opponent_id: str, is_conference: bool,
    wins: dict[str, int], losses: dict[str, int], point_differential: dict[str, int],
    conference_wins: dict[str, int], conference_losses: dict[str, int], team_last_completed_date: dict[str, str],
) -> None:
    """Credits entity_id's own side of one completed game into
    wins/losses/point_differential (and conference_wins/conference_losses,
    if this was a conference game) and updates team_last_completed_date --
    no-op if either side's own score is missing."""
    participant = next(p for p in event["participants"] if p.get("entity_id") == entity_id)
    opponent = next(p for p in event["participants"] if p.get("entity_id") == opponent_id)
    score = (participant.get("result") or {}).get("score")
    opponent_score = (opponent.get("result") or {}).get("score")
    if score is None or opponent_score is None:
        return
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


def _completed_game_records(completed: list[dict]) -> tuple[dict, dict, dict, dict, dict, dict, dict]:
    """(wins, losses, point_differential, team_last_completed_date,
    conference_wins, conference_losses, completed_by_team) derived from
    this season's own completed games."""
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
            _record_game_result(
                event, entity_id, opponent_id, is_conference, wins, losses, point_differential,
                conference_wins, conference_losses, team_last_completed_date,
            )

    for team_events in completed_by_team.values():
        team_events.sort(key=lambda e: e.get("event_date", ""), reverse=True)

    return wins, losses, point_differential, team_last_completed_date, conference_wins, conference_losses, completed_by_team


_team_rolling_stats = season_projection_common.team_rolling_stats


def _remaining_game_inputs(
    scheduled: list[dict], team_conference: dict[str, str],
) -> tuple[list[tuple[str, str, bool]], dict[str, str]]:
    """(remaining_games, team_next_event) from this season's own
    still-scheduled games, earliest first. remaining_games only keeps
    plain regular-season pairings (_is_regular_season_game) where both
    sides have a known conference."""
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
    return remaining_games, team_next_event


def _season_standings_inputs(storage: FeatureStorage, raw_bucket) -> dict:
    """Fetches this season's completed+scheduled events once and derives
    everything simulate_season and the ranking feature rows need.
    team_conference comes from schedule-sync's own daily S3 cache (see
    _load_cached_team_conference), not from event fields -- see this
    module's own docstring."""
    scheduled, all_completed, completed, current_season = _current_season_events(storage)
    team_conference = _load_cached_team_conference(raw_bucket, current_season)

    wins, losses, point_differential, team_last_completed_date, conference_wins, conference_losses, completed_by_team = (
        _completed_game_records(completed)
    )
    pre_game_ratings, current_ratings = compute_elo_ratings(all_completed, as_of_season=current_season)
    avg_points_scored, avg_points_allowed, win_streak, strength_of_schedule = _team_rolling_stats(
        completed_by_team, pre_game_ratings,
    )
    remaining_games, team_next_event = _remaining_game_inputs(scheduled, team_conference)

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
        "real_current_rank": _latest_ap_poll_ranks(raw_bucket, current_season),
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
    """Scores every team in one batched adapter.predict call (not one call per team)."""
    return season_projection_common.score_rows(
        estimator, model_card,
        {team_id: _ranking_feature_row(team_id, wins, losses, ratings, season_inputs) for team_id in teams},
    )


def _current_model_scores(estimator, model_card: dict, teams: list[str], season_inputs: dict) -> dict[str, float]:
    """Today's actual (not simulated) ranking-model score per team -- the
    SAME model/feature row simulate_season's own score_teams callable
    uses to pick each simulated season's March Madness field, scored once
    against real current wins/losses/ratings instead of a simulated
    future. Lower is better. Used by _model_rankings (standings'
    model_rank column) and by both real bracket payloads (seeding)."""
    return _batch_score_teams(
        estimator, model_card, teams, season_inputs,
        season_inputs["wins"], season_inputs["losses"], season_inputs["current_ratings"],
    )


def _model_rankings(estimator, model_card: dict, teams: list[str], season_inputs: dict) -> dict[str, int]:
    """The national-ranking model's own opinion of today's ranking --
    shown alongside the real AP poll rank (_latest_ap_poll_ranks) for
    comparison, not in place of it."""
    return season_projection_common.rank_by_score(teams, _current_model_scores(estimator, model_card, teams, season_inputs))


_RANKINGS_CACHE_KEY_RE = re.compile(r"^ncaambb/rankings/\d+/(\d+)/(\d+)\.json$")


def _latest_ap_poll_ranks(raw_bucket, season: int | None) -> dict[str, int]:
    """The real current AP Top 25 rank per team, from the most recently
    ingest-cached weekly poll for this season (ingest/handler.py's own
    _fetch_current_ap_poll writes one ncaambb/rankings/{season}/{type}/
    {week}.json object per week -- this Lambda has no route to ESPN
    itself, see this module's own docstring). Picks the highest (type,
    week) key present, since a later season_type (postseason) always
    follows every regular-season week chronologically. Empty if nothing's
    been cached yet for this season."""
    if season is None:
        return {}
    latest_key = None
    latest_sort_key = None
    for key in raw_bucket.list_keys(f"ncaambb/rankings/{season}/"):
        match = _RANKINGS_CACHE_KEY_RE.match(key)
        if not match:
            continue
        sort_key = (int(match.group(1)), int(match.group(2)))
        if latest_sort_key is None or sort_key > latest_sort_key:
            latest_sort_key, latest_key = sort_key, key
    if latest_key is None:
        return {}
    return ap_poll_to_rank_by_team(raw_bucket.get_json(latest_key))


FINAL_FOUR = "Final Four"
CHAMPIONSHIP = "Championship"
MARCH_MADNESS_COLUMNS = [
    ("Sweet 16", "Sweet 16"), ("Elite Eight", "Elite Eight"), (FINAL_FOUR, FINAL_FOUR),
    ("Final", CHAMPIONSHIP), ("Title", None),
]


def _real_postseason(storage: FeatureStorage, s3, predictions_table, current_season: int | None, is_game) -> RealPostseason:
    events = bracket_projection.real_postseason_events(storage, SPORT, current_season, is_game)
    return RealPostseason(events, bracket_projection.logged_home_probability(event_prediction, storage, s3, predictions_table, logger))


def _single_elim_rounds(play_round, seeded_teams: list[str], round_names: list[str], home_advantage: float):
    """The standard seed-line bracket for `seeded_teams` (byes where the
    field is short of a power of two), home advantage in the first round
    only. Returns (rounds, champion)."""
    bracket_size = season_simulation._next_power_of_two(len(seeded_teams))
    seed_line = season_simulation._standard_seed_line(bracket_size)
    slot_team: dict[int, str | None] = {
        seed: (seeded_teams[seed - 1] if seed <= len(seeded_teams) else None) for seed in seed_line
    }
    rounds, current_seeds, advantage, advancing = [], seed_line, home_advantage, []
    for round_name in round_names:
        pairs = [
            (slot_team[current_seeds[i]], slot_team[current_seeds[i + 1]], current_seeds[i], current_seeds[i + 1])
            for i in range(0, len(current_seeds), 2)
        ]
        round_payload, advancing = play_round(round_name, pairs, advantage)
        rounds.append(round_payload)
        for winner, winner_seed in advancing:
            slot_team[winner_seed] = winner
        current_seeds = [seed for _, seed in advancing]
        advantage = 0.0
    return rounds, advancing[0][0]


def _conference_bracket_payloads(
    storage: FeatureStorage, s3, predictions_table, season_inputs: dict, current_season: int | None,
) -> list[dict]:
    """One simulated bracket per conference with at least 2 tracked
    members -- {"conference": name, "bracket": {"rounds": [...],
    "champion": team_id, "round_odds": ...}}. Seeding is conference-only
    record (wins then point differential, see season_simulation.
    _conference_seed_order's own docstring for the simplification this
    accepts)."""
    conferences = season_simulation._group_by_conference(season_inputs["team_conference"])
    real = _real_postseason(storage, s3, predictions_table, current_season, _is_conference_tournament_game)
    rules = elo_rules(season_inputs["current_ratings"])

    payloads = []
    for conference, members in sorted(conferences.items()):
        if len(members) < 2:
            continue
        seed_order = season_simulation._conference_seed_order(
            members, season_inputs["conference_wins"], season_inputs["point_differential"],
        )
        round_names = season_simulation._round_names(season_simulation._next_power_of_two(len(seed_order)))

        def topology(play_round, seed_order=seed_order, round_names=round_names) -> dict:
            rounds, champion = _single_elim_rounds(play_round, seed_order, round_names, DEFAULT_HOME_ADVANTAGE)
            return {"rounds": rounds, "champion": champion}

        columns = [(name, name) for name in round_names[1:]] + [("Title", None)]
        try:
            bracket = BracketSimulation(real, rules).run(topology, columns, group_of=lambda team, conference=conference: conference)
        except Exception:
            logger.exception("Failed building conference bracket for %s -- skipping this run", conference)
            continue
        payloads.append({"conference": conference, "bracket": enrich_bracket_team_names(storage, SPORT, bracket)})
    return payloads


def _march_madness_bracket_payload(
    storage: FeatureStorage, s3, predictions_table, season_inputs: dict, current_season: int | None,
    estimator, model_card: dict, teams: list[str], conference_champions: dict[str, str],
) -> dict | None:
    """The March Madness bracket as one simulated run -- First Four seeded
    and resolved deterministically (no real First-Four results feed back in
    yet), then the 4 regions, the Final Four and the Championship. Each
    region's own round list is kept separate -- {"first_four", "regions":
    {name: {"rounds", "champion"}}, "final_four", "championship",
    "champion", "round_odds"} -- so the frontend can draw the traditional
    4-region layout. None if fewer than 2 conferences produced a champion
    (nothing to seed a field from)."""
    if len(conference_champions) < 2:
        return None

    model_scores = _current_model_scores(estimator, model_card, teams, season_inputs)
    auto_bids, at_large = season_simulation.select_march_madness_field(model_scores, conference_champions)
    first_four = season_simulation.project_first_four(auto_bids, at_large, season_inputs["current_ratings"], model_scores)
    regions = season_simulation._assign_regions(first_four["field"])
    region_of = {team: region for region, region_teams in regions.items() for team in region_teams}

    def topology(play_round) -> dict:
        region_brackets = {}
        for region_name in season_simulation.REGION_NAMES:
            region_teams = regions[region_name]
            size = season_simulation._next_power_of_two(len(region_teams))
            round_names = season_simulation.MARCH_MADNESS_REGION_ROUND_NAMES[: size.bit_length() - 1]
            rounds, champion = _single_elim_rounds(play_round, region_teams, round_names, 0.0)
            region_brackets[region_name] = {"rounds": rounds, "champion": champion}
        c = [region_brackets[name]["champion"] for name in season_simulation.REGION_NAMES]
        final_four, (f1, f2) = play_round(FINAL_FOUR, [(c[0], c[1], None, None), (c[2], c[3], None, None)], 0.0)
        championship, (champion,) = play_round(CHAMPIONSHIP, [(f1[0], f2[0], None, None)], 0.0)
        return {
            "first_four": first_four["matchups"],
            "regions": region_brackets,
            "final_four": final_four["matchups"],
            "championship": championship["matchups"][0],
            "champion": champion[0],
        }

    def tiebreak(team_a: str, team_b: str) -> str | None:
        score_a, score_b = model_scores.get(team_a), model_scores.get(team_b)
        if score_a is None or score_b is None or score_a == score_b:
            return None
        return team_a if score_a > score_b else team_b

    real = _real_postseason(storage, s3, predictions_table, current_season, _is_march_madness_game)
    engine = BracketSimulation(real, elo_rules(season_inputs["current_ratings"]), tiebreak=tiebreak)
    try:
        bracket = engine.run(topology, MARCH_MADNESS_COLUMNS, group_of=region_of.get)
    except Exception:
        # Exactly 4 regions by construction: without all of them there's no bracket to show.
        logger.exception("Failed building the March Madness bracket -- skipping this run")
        return None
    return enrich_bracket_team_names(storage, SPORT, bracket)


def _compute_projections_and_brackets(
    estimator, model_card, teams: list[str], season_inputs: dict, storage: FeatureStorage, s3, predictions_table,
    current_season: int | None,
) -> tuple[dict, dict, list[dict], dict | None]:
    """(simulation, model_rankings, conference_brackets,
    march_madness_bracket) for one season projection run -- the Monte
    Carlo season simulation, model rankings, conference brackets, and
    March Madness bracket, each independently try/excepted so one failing
    doesn't cost the others (or the plain standings build_season_
    projection can still build without any of them)."""
    def score_teams(wins: dict, losses: dict, ratings: dict) -> dict[str, float]:
        return _batch_score_teams(estimator, model_card, teams, season_inputs, wins, losses, ratings)

    simulation: dict[str, dict] = {}
    try:
        simulation = season_simulation.simulate_season(
            season_inputs["wins"], season_inputs["losses"],
            season_inputs["conference_wins"], season_inputs["conference_losses"],
            season_inputs["point_differential"], season_inputs["remaining_games"],
            season_inputs["current_ratings"], season_inputs["team_conference"], score_teams,
        )
    except Exception:
        logger.exception("Failed running the Monte Carlo season simulation -- standings will omit projected/probability columns this run")

    model_rankings: dict[str, int] = {}
    try:
        model_rankings = _model_rankings(estimator, model_card, teams, season_inputs)
    except Exception:
        logger.exception("Failed to compute model_rank -- standings will omit it this run")

    conference_brackets: list[dict] = []
    try:
        conference_brackets = _conference_bracket_payloads(storage, s3, predictions_table, season_inputs, current_season)
    except Exception:
        logger.exception("Failed building conference brackets")

    conference_champions = {payload["conference"]: payload["bracket"]["champion"] for payload in conference_brackets}
    march_madness_bracket = None
    try:
        march_madness_bracket = _march_madness_bracket_payload(
            storage, s3, predictions_table, season_inputs, current_season,
            estimator, model_card, teams, conference_champions,
        )
    except Exception:
        logger.exception("Failed building the March Madness bracket")

    return simulation, model_rankings, conference_brackets, march_madness_bracket


def build_season_projection(storage: FeatureStorage, s3, predictions_table, raw_bucket) -> dict:
    season_inputs = _season_standings_inputs(storage, raw_bucket)
    teams = list(season_inputs["team_conference"])
    current_season = season_inputs["current_season"]

    simulation: dict[str, dict] = {}
    model_rankings: dict[str, int] = {}
    conference_brackets: list[dict] = []
    march_madness_bracket = None

    if teams:
        try:
            estimator, model_card = model_loader.load_current_model(s3, SPORT, RANKING_MODEL_NAME)
        except model_loader.NoPromotedModelError:
            logger.warning("No promoted %s model -- season simulation, rankings, and both brackets skipped this run", RANKING_MODEL_NAME)
            estimator = model_card = None

        if estimator is not None:
            simulation, model_rankings, conference_brackets, march_madness_bracket = _compute_projections_and_brackets(
                estimator, model_card, teams, season_inputs, storage, s3, predictions_table, current_season,
            )

    standings = sorted(
        (
            {
                "team_id": team_id,
                "conference": season_inputs["team_conference"].get(team_id),
                "wins": season_inputs["wins"].get(team_id, 0),
                "losses": season_inputs["losses"].get(team_id, 0),
                "current_rank": season_inputs["real_current_rank"].get(team_id),
                "model_rank": model_rankings.get(team_id),
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
