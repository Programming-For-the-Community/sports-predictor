"""
Regular-season Monte Carlo simulation (win totals, bowl eligibility,
conference championship, CFP field, and national championship
probabilities) and player-prop leaderboard projection for the NCAAFB
Season tab. Pure functions -- no AWS/storage access, same testability
philosophy as Source/aws-lambdas/nfl/predict/season_simulation.py's own
docstring -- but NOT a port of it, since FBS has no static division
table (conference membership changes ~yearly -- see
library/features/ncaafb.py's own docstring) and no fixed playoff bracket
seeded by division winners the way the NFL's 7-seed-per-conference
format is.

Game outcomes use Elo win probability (library.features.common.
expected_score), the same "recomputing the full trained model for
thousands of Monte Carlo paths is intractable" reasoning
nfl/predict/season_simulation.py's own docstring gives.

CFP field selection is different: it genuinely NEEDS something rank-like
to pick 5 conference-champion auto-bids + 7 at-large teams the way the
real committee would, and Elo alone is a poor stand-in for that (it has
no notion of quality wins, strength of schedule, or an AP-style
consensus). This module stays pure and takes a `score_teams` callable
instead of computing that itself -- Source/aws-lambdas/ncaafb/predict/
season_projection.py's own caller supplies one backed by the actual
trained national-ranking model, scored once per iteration in a single
batched call across every team (not once per team -- see that module's
own docstring for why that's what makes it tractable at all).

Conference champion: the team with the best (wins, point_differential)
record among its own conference's tracked members -- SIMPLIFIED the same
way nfl/predict/season_simulation.py's own division-winner selection is
(no real conference-championship-game simulation, no head-to-head/
common-opponent tiebreakers). point_differential is today's REAL value,
not simulated forward, same simplification NFL's own module documents.

CFP bracket: 12 teams -- the 4 highest-ranked conference champions get a
bye and the top 4 seeds; the 5th-highest-ranked champion joins the 7
at-large teams to fill seeds 5-12. Round of 12 is real campus-site games
(home_advantage applies, higher seed hosts); quarterfinals onward are
neutral-site bowl games (no home_advantage), in the 12-team format's own
fixed (non-reseeded) bracket: 1 vs (8/9 winner), 2 vs (5/12 winner), 3 vs
(6/11 winner), 4 vs (7/10 winner). A best-effort model of the format
introduced for the 2024 season, not adjusted for its exact committee-set
bowl-site assignments (which don't affect who wins) -- worth confirming
against the real bracket rules if this drifts from reality.

Bowl eligibility: 6+ wins, the standard bar -- doesn't implement the FCS-
win-counts-once-per-two-years nuance or waiver exceptions for a team that
can't reach 6 (e.g. a killed game).
"""
import random
from typing import Callable

from library.features.common import DEFAULT_HOME_ADVANTAGE, DEFAULT_K_FACTOR, DEFAULT_STARTING_RATING, expected_score

DEFAULT_SIMULATIONS = 1000
BOWL_ELIGIBILITY_WINS = 6
CFP_FIELD_SIZE = 12
CFP_AUTO_BIDS = 5
CFP_BYES = 4


def _group_by_conference(team_conference: dict[str, str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for team_id, conference in team_conference.items():
        if conference:
            result.setdefault(conference, []).append(team_id)
    return result


def _conference_champion(members: list[str], wins: dict[str, int], point_differential: dict[str, int]) -> str:
    def sort_key(team_id: str) -> tuple[int, int]:
        return (wins.get(team_id, 0), point_differential.get(team_id, 0))

    return max(members, key=sort_key)


def _select_cfp_field(model_scores: dict[str, float], champions: dict[str, str]) -> list[str]:
    """Returns 12 seeds in order (seed 1 first) -- see this module's own
    docstring for the auto-bid/at-large/bye rules. Lower model_scores is
    better (the ranking model predicts AP-style rank, where 1 is best);
    a team missing from model_scores entirely sorts last."""
    ranked_champs = sorted(set(champions.values()), key=lambda t: model_scores.get(t, float("inf")))
    auto_bids = ranked_champs[: min(CFP_AUTO_BIDS, len(ranked_champs))]

    pool = sorted((t for t in model_scores if t not in auto_bids), key=lambda t: model_scores[t])
    at_large = pool[: max(0, CFP_FIELD_SIZE - len(auto_bids))]
    field = auto_bids + at_large

    bye_seeds = sorted(auto_bids, key=lambda t: model_scores.get(t, float("inf")))[: min(CFP_BYES, len(auto_bids))]
    rest = sorted((t for t in field if t not in bye_seeds), key=lambda t: model_scores.get(t, float("inf")))
    return bye_seeds + rest


def _play(team_a: str, team_b: str, ratings: dict[str, float], home_advantage: float, rng: random.Random, neutral: bool = False) -> str:
    rating_a = ratings.get(team_a, DEFAULT_STARTING_RATING)
    rating_b = ratings.get(team_b, DEFAULT_STARTING_RATING)
    advantage = 0.0 if neutral else home_advantage
    probability_a = expected_score(rating_a, rating_b, advantage)
    return team_a if rng.random() < probability_a else team_b


def _simulate_cfp_bracket(seeds: list[str], ratings: dict[str, float], home_advantage: float, rng: random.Random) -> str:
    """Returns the national champion for one simulated 12-team CFP --
    see this module's own docstring for the bracket shape."""
    one, two, three, four, five, six, seven, eight, nine, ten, eleven, twelve = seeds

    r1_5v12 = _play(five, twelve, ratings, home_advantage, rng)
    r1_6v11 = _play(six, eleven, ratings, home_advantage, rng)
    r1_7v10 = _play(seven, ten, ratings, home_advantage, rng)
    r1_8v9 = _play(eight, nine, ratings, home_advantage, rng)

    qf1 = _play(one, r1_8v9, ratings, home_advantage, rng, neutral=True)
    qf2 = _play(two, r1_5v12, ratings, home_advantage, rng, neutral=True)
    qf3 = _play(three, r1_6v11, ratings, home_advantage, rng, neutral=True)
    qf4 = _play(four, r1_7v10, ratings, home_advantage, rng, neutral=True)

    sf1 = _play(qf1, qf4, ratings, home_advantage, rng, neutral=True)
    sf2 = _play(qf2, qf3, ratings, home_advantage, rng, neutral=True)

    return _play(sf1, sf2, ratings, home_advantage, rng, neutral=True)


def simulate_season(
    current_wins: dict[str, int],
    current_losses: dict[str, int],
    point_differential: dict[str, int],
    remaining_games: list[tuple[str, str]],
    current_ratings: dict[str, float],
    team_conference: dict[str, str],
    score_teams: Callable[[dict[str, int], dict[str, int], dict[str, float]], dict[str, float]],
    simulations: int = DEFAULT_SIMULATIONS,
    home_advantage: float = DEFAULT_HOME_ADVANTAGE,
    k_factor: float = DEFAULT_K_FACTOR,
    rng: random.Random | None = None,
) -> dict[str, dict]:
    """Runs `simulations` full Monte Carlo season paths from today's
    actual record forward -- see this module's own docstring for what
    each of the five projected probabilities means and why `score_teams`
    is a caller-supplied callable rather than something this module
    computes itself.

    team_conference is this season's real {team_id: conference} map (see
    Source/aws-lambdas/ncaafb/predict/season_projection.py's own
    _season_standings_inputs) -- the full set of tracked teams is derived
    from its keys, unlike NFL's own simulate_season which pulls the full
    32-team league from a static TEAM_DIVISIONS constant. remaining_games
    should already be filtered to real FBS-vs-FBS matchups only (both
    sides present in team_conference) -- an FCS opponent has no
    meaningful Elo rating or conference to simulate against.

    score_teams is called once per simulated season with that
    iteration's final (wins, losses, ratings) and must return a
    {team_id: score} dict (lower is better) for every team_id in
    team_conference -- used only to pick the CFP field, not the
    bracket's own game outcomes (those stay Elo-based like every other
    game here). No `projected_losses` derivation assumption about season
    length -- tracked directly in the loop, same reasoning NFL's own
    module gives.
    """
    rng = rng or random.Random()
    teams = set(team_conference)
    conferences = _group_by_conference(team_conference)

    win_totals = {team_id: 0.0 for team_id in teams}
    loss_totals = {team_id: 0.0 for team_id in teams}
    conference_champion_totals = {team_id: 0 for team_id in teams}
    bowl_totals = {team_id: 0 for team_id in teams}
    cfp_totals = {team_id: 0 for team_id in teams}
    championship_totals = {team_id: 0 for team_id in teams}

    for _ in range(simulations):
        wins = {team_id: current_wins.get(team_id, 0) for team_id in teams}
        losses = {team_id: current_losses.get(team_id, 0) for team_id in teams}
        ratings = dict(current_ratings)

        for home_id, away_id in remaining_games:
            if home_id not in teams or away_id not in teams:
                continue  # not a real FBS-vs-FBS matchup -- see this function's own docstring
            home_rating = ratings.get(home_id, DEFAULT_STARTING_RATING)
            away_rating = ratings.get(away_id, DEFAULT_STARTING_RATING)
            home_win_probability = expected_score(home_rating, away_rating, home_advantage)
            home_won = rng.random() < home_win_probability

            wins[home_id if home_won else away_id] += 1
            losses[away_id if home_won else home_id] += 1

            actual_home = 1.0 if home_won else 0.0
            ratings[home_id] = home_rating + k_factor * (actual_home - home_win_probability)
            ratings[away_id] = away_rating + k_factor * ((1 - actual_home) - (1 - home_win_probability))

        for team_id in teams:
            win_totals[team_id] += wins[team_id]
            loss_totals[team_id] += losses[team_id]
            if wins[team_id] >= BOWL_ELIGIBILITY_WINS:
                bowl_totals[team_id] += 1

        champions = {
            conference: _conference_champion(members, wins, point_differential)
            for conference, members in conferences.items()
        }
        for champion_team in champions.values():
            conference_champion_totals[champion_team] += 1

        model_scores = score_teams(wins, losses, ratings)
        seeds = _select_cfp_field(model_scores, champions)
        for team_id in seeds:
            cfp_totals[team_id] += 1

        champion = _simulate_cfp_bracket(seeds, ratings, home_advantage, rng)
        championship_totals[champion] += 1

    return {
        team_id: {
            "projected_wins": win_totals[team_id] / simulations,
            "projected_losses": loss_totals[team_id] / simulations,
            "conference_champion_probability": conference_champion_totals[team_id] / simulations,
            "bowl_probability": bowl_totals[team_id] / simulations,
            "playoff_probability": cfp_totals[team_id] / simulations,
            "championship_probability": championship_totals[team_id] / simulations,
        }
        for team_id in teams
    }


def project_leaderboard(
    current_totals: dict[str, float],
    per_game_projections: dict[str, float],
    games_remaining: dict[str, int],
    top_n: int = 10,
) -> list[dict]:
    """Identical to nfl/predict/season_simulation.py's own function of
    the same name -- see its own docstring. Duplicated, not imported,
    same per-Lambda-deployment-package convention as every other shared
    shape in this file."""
    projected = []
    for entity_id, current_total in current_totals.items():
        per_game = per_game_projections.get(entity_id, 0.0)
        remaining = games_remaining.get(entity_id, 0)
        projected.append({
            "entity_id": entity_id,
            "current_total": current_total,
            "projected_total": current_total + per_game * remaining,
        })
    projected.sort(key=lambda row: row["projected_total"], reverse=True)
    return projected[:top_n]
