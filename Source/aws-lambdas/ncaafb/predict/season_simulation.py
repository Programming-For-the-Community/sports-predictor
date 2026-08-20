"""
Regular-season Monte Carlo simulation (win totals, bowl eligibility,
conference championship, CFP field, and national championship
probabilities) for the NCAAFB Season tab -- team outcomes only, no
player-prop leaderboard. Pure functions -- no AWS/storage access.

Game outcomes use Elo win probability (library.features.common.
expected_score). CFP field selection needs a rank-like score per team,
which Elo alone can't provide -- callers pass a `score_teams` callable
backed by the real trained national-ranking model.

Conference champion: best (wins, point_differential) record among a
conference's tracked members. No real championship-game simulation, no
head-to-head/common-opponent tiebreakers. point_differential is today's
real value, not simulated forward.

CFP bracket: 12 teams. The 4 highest-ranked conference champions get a
bye and seeds 1-4; the 5th-ranked champion joins the 7 at-large teams for
seeds 5-12. Round of 12 is campus-site (home_advantage applies, higher
seed hosts); quarterfinals on are neutral-site, fixed (non-reseeded)
bracket: 1 vs (8/9 winner), 2 vs (5/12 winner), 3 vs (6/11 winner), 4 vs
(7/10 winner).

Bowl eligibility: 6+ wins. Doesn't implement the FCS-win or waiver
nuances.
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
    """12 seeds in order (seed 1 first). Lower model_scores is better; a team missing from
    model_scores sorts last."""
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
    """Returns the national champion for one simulated 12-team CFP."""
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


def project_matchup(
    team_a: str, team_b: str, seed_a: int | None, seed_b: int | None,
    ratings: dict[str, float], home_advantage: float,
) -> dict:
    """Deterministic single-matchup resolution -- no RNG, picks whichever
    side has >= 50% win probability. When both seeds are known, the
    better (numerically lower) seed always hosts (Round of 12's own
    campus-site convention); if either is None, or home_advantage is
    passed as 0.0 (every round from the Quarterfinals on is neutral-site,
    see project_cfp_bracket), team_a/team_b keep their given order and
    home_advantage itself decides the framing.

    Returns {"team_a", "team_b", "seed_a", "seed_b", "predicted_winner",
    "win_probability"} -- win_probability is always the WINNER's own
    probability, not "team_a's".
    """
    if seed_a is not None and seed_b is not None and seed_b < seed_a:
        team_a, team_b, seed_a, seed_b = team_b, team_a, seed_b, seed_a
    rating_a = ratings.get(team_a, DEFAULT_STARTING_RATING)
    rating_b = ratings.get(team_b, DEFAULT_STARTING_RATING)
    probability_a = expected_score(rating_a, rating_b, home_advantage)
    winner = team_a if probability_a >= 0.5 else team_b
    return {
        "team_a": team_a,
        "team_b": team_b,
        "seed_a": seed_a,
        "seed_b": seed_b,
        "predicted_winner": winner,
        "win_probability": probability_a if winner == team_a else 1 - probability_a,
    }


def project_cfp_bracket(seeds: list[str], ratings: dict[str, float], home_advantage: float = DEFAULT_HOME_ADVANTAGE) -> dict:
    """Deterministic 12-team CFP bracket: seeds 1-4 get a bye; Round of
    12 is campus-site (5v12/6v11/7v10/8v9); later rounds are neutral-site
    and fixed (no reseeding). Picks the higher-win-probability side each
    matchup and returns the full round-by-round path.

    Returns {"rounds": [{"round": "Round of 12", "matchups": [...]},
    ...], "champion": team_id}.
    """
    seed_number = {team_id: rank + 1 for rank, team_id in enumerate(seeds)}
    one, two, three, four, five, six, seven, eight, nine, ten, eleven, twelve = seeds

    round_of_12_pairs = [(five, twelve), (six, eleven), (seven, ten), (eight, nine)]
    round_of_12_matchups = [
        project_matchup(a, b, seed_number[a], seed_number[b], ratings, home_advantage) for a, b in round_of_12_pairs
    ]
    r1_5v12, r1_6v11, r1_7v10, r1_8v9 = (m["predicted_winner"] for m in round_of_12_matchups)

    quarterfinal_pairs = [(one, r1_8v9), (two, r1_5v12), (three, r1_6v11), (four, r1_7v10)]
    quarterfinal_matchups = [
        project_matchup(a, b, seed_number.get(a), seed_number.get(b), ratings, 0.0) for a, b in quarterfinal_pairs
    ]
    qf1, qf2, qf3, qf4 = (m["predicted_winner"] for m in quarterfinal_matchups)

    semifinal_matchups = [
        project_matchup(qf1, qf4, None, None, ratings, 0.0),
        project_matchup(qf2, qf3, None, None, ratings, 0.0),
    ]
    sf1, sf2 = (m["predicted_winner"] for m in semifinal_matchups)

    championship_matchup = project_matchup(sf1, sf2, None, None, ratings, 0.0)

    return {
        "rounds": [
            {"round": "Round of 12", "matchups": round_of_12_matchups},
            {"round": "Quarterfinals", "matchups": quarterfinal_matchups},
            {"round": "Semifinals", "matchups": semifinal_matchups},
            {"round": "National Championship", "matchups": [championship_matchup]},
        ],
        "champion": championship_matchup["predicted_winner"],
    }


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
    """Runs `simulations` Monte Carlo season paths from today's actual record forward.

    team_conference is {team_id: conference}; the tracked team set is
    derived from its keys. remaining_games must already be filtered to
    FBS-vs-FBS matchups (both sides present in team_conference).

    score_teams is called once per simulated season with that
    iteration's final (wins, losses, ratings) and must return a
    {team_id: score} dict (lower is better) for every tracked team --
    used only to pick the CFP field, not game outcomes.
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
                continue
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
