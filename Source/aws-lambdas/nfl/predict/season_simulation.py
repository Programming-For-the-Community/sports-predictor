"""
Regular-season Monte Carlo simulation (win totals, division winners,
playoff field, Super Bowl pick) and player-prop leaderboard projection
for the NFL Season tab. Pure functions -- no AWS/storage access, same
testability philosophy as library/features/nfl.py's functions -- but
lives here, not there, since none of this runs at training time; it
exists purely to answer a serving-time question ("how does the rest of
the season likely play out").

Uses Elo win probability (library.features.nfl.expected_score), NOT the
full trained XGBoost win-probability model. The trained model needs a
full feature vector (rolling averages, QB/RB/WR identity, rest days...)
that depends on how the season actually unfolds -- reconstructing that
for thousands of Monte Carlo paths through the rest of a season is
intractable for an on-request Lambda call with no caching. Elo already
captures relative team strength and updates in closed form, so it's the
only approach compatible with "recomputed on request".

Playoff seeding is SIMPLIFIED: 4 division winners + 3 best-remaining-
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

    Returns {team_id: {"projected_wins": float, "division_winner_probability":
    float, "playoff_probability": float, "championship_probability": float}}.

    Simulated rating updates use a plain K-factor adjustment, no
    margin-of-victory scaling (compute_elo_ratings' own MOV multiplier
    needs a real score, which this never generates -- only a win/loss
    draw) -- a documented simplification, not an oversight.
    """
    rng = rng or random.Random()
    # Always the full real league (TEAM_DIVISIONS' own 32 teams), not just
    # whoever appears in current_wins/remaining_games -- _seed_conference
    # picks division winners/wildcards from the FULL division rosters
    # below, and every team in a division needs an entry in every
    # aggregate dict even if it wasn't explicitly passed in (e.g. a team
    # with a bye and no remaining games still needs a projection).
    teams = set(TEAM_DIVISIONS)

    win_totals = {team_id: 0.0 for team_id in teams}
    division_titles = {team_id: 0 for team_id in teams}
    playoff_berths = {team_id: 0 for team_id in teams}
    championships = {team_id: 0 for team_id in teams}

    conferences = _divisions_by_conference()

    for _ in range(simulations):
        wins = {team_id: current_wins.get(team_id, 0) for team_id in teams}
        ratings = dict(current_ratings)

        for home_id, away_id in remaining_games:
            home_rating = ratings.get(home_id, DEFAULT_STARTING_RATING)
            away_rating = ratings.get(away_id, DEFAULT_STARTING_RATING)
            home_win_probability = expected_score(home_rating, away_rating, home_advantage)
            home_won = rng.random() < home_win_probability

            wins[home_id if home_won else away_id] += 1

            actual_home = 1.0 if home_won else 0.0
            ratings[home_id] = home_rating + k_factor * (actual_home - home_win_probability)
            ratings[away_id] = away_rating + k_factor * ((1 - actual_home) - (1 - home_win_probability))

        for team_id in teams:
            win_totals[team_id] += wins[team_id]

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
    remaining game) -- not a per-opponent simulation, which would
    multiply the same intractability problem simulate_season's own
    docstring explains. Returns the top_n by projected total, descending.
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
