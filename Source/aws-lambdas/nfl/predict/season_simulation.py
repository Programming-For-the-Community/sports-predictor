"""
Regular-season Monte Carlo simulation (win totals, division winners,
playoff field, Super Bowl pick) and player-prop leaderboard projection
for the NFL Season tab. Pure functions -- no AWS/storage access.

Uses Elo win probability (library.features.nfl.expected_score), not the
full trained XGBoost win-probability model: Elo captures relative team
strength and updates in closed form, cheap enough for thousands of
Monte Carlo paths through the rest of a season.

Playoff seeding is simplified: 4 division winners + 3 best-remaining-
record wildcards per conference, tiebroken by each team's real (not
simulated) point differential as of today. The NFL's actual tiebreaker
rules (head-to-head, strength of victory, common games, conference
record, etc.) are not implemented.
"""
import random

from library.features.common import DEFAULT_HOME_ADVANTAGE, DEFAULT_K_FACTOR, DEFAULT_STARTING_RATING, expected_score
from library.features.nfl_teams import TEAM_DIVISIONS

DEFAULT_SIMULATIONS = 2000
PLAYOFF_SEEDS_PER_CONFERENCE = 7


def _conference(division: str) -> str:
    return division.split()[0]  # "AFC East" -> "AFC"


def _divisions_by_conference() -> dict[str, dict[str, list[str]]]:
    """{"AFC": {"AFC East": [team_id, ...], ...}, "NFC": {...}}"""
    result: dict[str, dict[str, list[str]]] = {}
    for team_id, division in TEAM_DIVISIONS.items():
        conference = _conference(division)
        result.setdefault(conference, {}).setdefault(division, []).append(team_id)
    return result


def _seed_conference(
    division_teams: dict[str, list[str]], wins: dict[str, int], point_differential: dict[str, int],
) -> tuple[list[str], set[str]]:
    """Returns (seeds 1-7 in order, the set of division winners among
    them) for one conference. Seed 1 is the division winner with the
    best record -- ties broken by point differential, then arbitrarily
    (dict/list order) if still tied, since this simplified seeding
    doesn't implement the NFL's real tiebreaker chain."""

    def sort_key(team_id: str) -> tuple[int, int]:
        return (wins.get(team_id, 0), point_differential.get(team_id, 0))

    division_winners = {max(teams, key=sort_key) for teams in division_teams.values()}
    seeds = sorted(division_winners, key=sort_key, reverse=True)

    remaining = [
        team_id
        for teams in division_teams.values()
        for team_id in teams
        if team_id not in division_winners
    ]
    wildcards = sorted(remaining, key=sort_key, reverse=True)[: PLAYOFF_SEEDS_PER_CONFERENCE - len(seeds)]
    seeds.extend(wildcards)
    return seeds, division_winners


def _simulate_bracket(seeds: list[str], ratings: dict[str, float], home_advantage: float, rng: random.Random) -> str:
    """Standard 7-team single-elimination bracket with reseeding each
    round -- seed 1 has a bye; 2v7, 3v6, 4v5 in the wild-card round;
    seed 1 then hosts the lowest remaining seed while the other two
    survivors play each other; winners meet for the conference title.
    The higher (numerically lower) remaining seed always hosts."""
    seed_rank = {team_id: rank for rank, team_id in enumerate(seeds)}

    def play(team_a: str, team_b: str) -> str:
        home, away = (team_a, team_b) if seed_rank[team_a] < seed_rank[team_b] else (team_b, team_a)
        home_rating = ratings.get(home, DEFAULT_STARTING_RATING)
        away_rating = ratings.get(away, DEFAULT_STARTING_RATING)
        home_win_probability = expected_score(home_rating, away_rating, home_advantage)
        return home if rng.random() < home_win_probability else away

    one, two, three, four, five, six, seven = seeds
    wild_card_winners = [play(two, seven), play(three, six), play(four, five)]

    divisional_field = sorted(wild_card_winners, key=lambda team_id: seed_rank[team_id])
    lowest_remaining_seed = divisional_field[-1]
    other_two = divisional_field[:-1]
    divisional_winners = [play(one, lowest_remaining_seed), play(other_two[0], other_two[1])]

    return play(divisional_winners[0], divisional_winners[1])


def project_matchup(
    team_a: str, team_b: str, seed_a: int | None, seed_b: int | None,
    ratings: dict[str, float], home_advantage: float,
) -> dict:
    """Deterministic single-matchup resolution -- no RNG, picks whichever
    side has >= 50% win probability. When both seeds are known, the
    better (numerically lower) seed always hosts; if either is None (e.g.
    a neutral-site game), team_a/team_b keep their given order and
    home_advantage itself decides the game's home/neutral framing (pass
    0.0 for a genuinely neutral-site game).

    Returns {"team_a", "team_b", "seed_a", "seed_b", "predicted_winner",
    "win_probability"} -- team_a/team_b reflect whichever side actually
    ended up "home" above, and win_probability is always the winner's
    own probability, not "team_a's".
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


def project_bracket(seeds: list[str], ratings: dict[str, float], home_advantage: float = DEFAULT_HOME_ADVANTAGE) -> dict:
    """Deterministic 7-team playoff bracket: seed 1 has a bye; 2v7/3v6/4v5
    in the wild-card round; seed 1 then plays the lowest remaining seed
    while the other two survivors play each other in the divisional
    round; winners meet for the conference title. Picks the
    higher-win-probability side every matchup and returns the full
    round-by-round path.

    Returns {"rounds": [{"round": "Wild Card", "matchups": [...]}, ...],
    "champion": team_id}.
    """
    seed_number = {team_id: rank + 1 for rank, team_id in enumerate(seeds)}
    seed_rank = {team_id: rank for rank, team_id in enumerate(seeds)}

    def play(team_a: str, team_b: str) -> tuple[str, dict]:
        matchup = project_matchup(team_a, team_b, seed_number[team_a], seed_number[team_b], ratings, home_advantage)
        return matchup["predicted_winner"], matchup

    one, two, three, four, five, six, seven = seeds
    wild_card_winners = []
    wild_card_matchups = []
    for a, b in ((two, seven), (three, six), (four, five)):
        winner, matchup = play(a, b)
        wild_card_winners.append(winner)
        wild_card_matchups.append(matchup)

    divisional_field = sorted(wild_card_winners, key=lambda team_id: seed_rank[team_id])
    lowest_remaining_seed = divisional_field[-1]
    other_two = divisional_field[:-1]
    divisional_winners = []
    divisional_matchups = []
    for a, b in ((one, lowest_remaining_seed), (other_two[0], other_two[1])):
        winner, matchup = play(a, b)
        divisional_winners.append(winner)
        divisional_matchups.append(matchup)

    champion, championship_matchup = play(divisional_winners[0], divisional_winners[1])

    return {
        "rounds": [
            {"round": "Wild Card", "matchups": wild_card_matchups},
            {"round": "Divisional", "matchups": divisional_matchups},
            {"round": "Conference Championship", "matchups": [championship_matchup]},
        ],
        "champion": champion,
    }


def project_full_bracket(
    conference_seeds: dict[str, list[str]], ratings: dict[str, float], home_advantage: float = DEFAULT_HOME_ADVANTAGE,
) -> dict:
    """Both conferences' own project_bracket walk, plus a deterministic
    Super Bowl pick: rating-based win probability at neutral-site odds
    (home_advantage=0.0), favoring whichever conference champion rates
    higher. seed_a/seed_b are None on the Super Bowl matchup row -- the
    two champions' conference seeds aren't comparable on one shared
    scale."""
    conference_results = {
        conference: project_bracket(seeds, ratings, home_advantage) for conference, seeds in conference_seeds.items()
    }
    (conference_a, result_a), (conference_b, result_b) = conference_results.items()
    super_bowl_matchup = project_matchup(result_a["champion"], result_b["champion"], None, None, ratings, 0.0)

    return {
        "conferences": {conference: result["rounds"] for conference, result in conference_results.items()},
        "super_bowl": super_bowl_matchup,
        "champion": super_bowl_matchup["predicted_winner"],
    }


def simulate_season(
    current_wins: dict[str, int],
    current_losses: dict[str, int],
    point_differential: dict[str, int],
    remaining_games: list[tuple[str, str]],
    current_ratings: dict[str, float],
    simulations: int = DEFAULT_SIMULATIONS,
    home_advantage: float = DEFAULT_HOME_ADVANTAGE,
    k_factor: float = DEFAULT_K_FACTOR,
    rng: random.Random | None = None,
) -> dict[str, dict]:
    """Runs `simulations` full Monte Carlo season paths from today's
    actual record forward. remaining_games is a flat, chronologically
    ordered list of (home_id, away_id) pairs -- each game appears once,
    not once per team.

    Returns {team_id: {"projected_wins": float, "projected_losses": float,
    "division_winner_probability": float, "playoff_probability": float,
    "championship_probability": float}}. projected_losses is tracked
    directly in the simulation loop rather than derived from
    projected_wins and a hardcoded season length. No projected_ties --
    home_won below is a strict win/loss draw.

    Simulated rating updates use a plain K-factor adjustment, no
    margin-of-victory scaling, since no real score is ever generated.
    """
    rng = rng or random.Random()
    # Always the full real league (TEAM_DIVISIONS' own 32 teams), not just
    # whoever appears in current_wins/remaining_games -- every team in a
    # division needs an entry in every aggregate dict even if it wasn't
    # explicitly passed in.
    teams = set(TEAM_DIVISIONS)

    win_totals = {team_id: 0.0 for team_id in teams}
    loss_totals = {team_id: 0.0 for team_id in teams}
    division_titles = {team_id: 0 for team_id in teams}
    playoff_berths = {team_id: 0 for team_id in teams}
    championships = {team_id: 0 for team_id in teams}

    conferences = _divisions_by_conference()

    for _ in range(simulations):
        wins = {team_id: current_wins.get(team_id, 0) for team_id in teams}
        losses = {team_id: current_losses.get(team_id, 0) for team_id in teams}
        ratings = dict(current_ratings)

        for home_id, away_id in remaining_games:
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

        for conference, division_teams in conferences.items():
            seeds, division_winners = _seed_conference(division_teams, wins, point_differential)
            for team_id in division_winners:
                division_titles[team_id] += 1
            for team_id in seeds:
                playoff_berths[team_id] += 1

        conference_champions = []
        for conference, division_teams in conferences.items():
            seeds, _ = _seed_conference(division_teams, wins, point_differential)
            conference_champions.append(_simulate_bracket(seeds, ratings, home_advantage, rng))
        # Super Bowl -- neither side has a "home" designation in the real
        # NFL (a fixed pre-determined host), so this plays it at even odds.
        champion = conference_champions[0] if rng.random() < 0.5 else conference_champions[1]
        championships[champion] += 1

    return {
        team_id: {
            "projected_wins": win_totals[team_id] / simulations,
            "projected_losses": loss_totals[team_id] / simulations,
            "division_winner_probability": division_titles[team_id] / simulations,
            "playoff_probability": playoff_berths[team_id] / simulations,
            "championship_probability": championships[team_id] / simulations,
        }
        for team_id in teams
    }


def project_leaderboard(
    current_totals: dict[str, float],
    per_game_projections: dict[str, float],
    games_remaining: dict[str, int],
    top_n: int = 10,
) -> list[dict]:
    """Projects each candidate's season-end total as their current total
    plus a flat per-remaining-game estimate (their own player-prop
    model's prediction for their team's next game, applied across every
    remaining game) rather than a per-opponent simulation. Returns the
    top_n by projected total, descending.
    """
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
