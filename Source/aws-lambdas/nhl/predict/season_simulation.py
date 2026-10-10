"""
Monte Carlo NHL season simulation and the projected playoff bracket --
pure functions, no AWS calls. season_projection.py supplies the records,
remaining schedule and Elo ratings.

What makes it hockey:
- Standings run on points: 2 for a win, 1 for an overtime or shootout
  loss, 0 for a regulation loss. Each simulated game is decided by the
  Elo win probability, then goes to overtime with the league-wide
  OVERTIME_RATE (there is no overtime model).
- Ties in the standings break on regulation wins, then total wins.
- Each conference sends its two divisions' top three plus two wildcards.
  The bracket is fixed, not reseeded: the better division winner plays
  the second wildcard, the other plays the first, and each division's
  second and third seeds play each other.
- Every series is best of seven, the higher seed hosting games 1, 2, 5
  and 7.
"""
import random

from library.features import nhl_teams
from library.features.common import DEFAULT_STARTING_RATING, expected_score
from library.features.nhl import ELO_HOME_ADVANTAGE
from library.serving import season_projection_common

DEFAULT_SIMULATIONS = 2000
# Share of regular-season games tied after regulation, 2015-16..2025-26.
OVERTIME_RATE = 0.225

DIVISION_SEEDS = 3
WILDCARDS = 2
SERIES_WINS = 4
# Whether the higher seed is at home, game by game.
HIGHER_SEED_HOME = (True, True, False, False, True, False, True)

ROUND_NAMES = ("First Round", "Second Round", "Conference Final")
FINAL_ROUND = "Stanley Cup Final"


def teams_by_division() -> dict[str, list[str]]:
    """Current teams by division (a relocated franchise's old id left out)."""
    divisions: dict[str, list[str]] = {}
    for team_id, division in nhl_teams.TEAM_DIVISIONS.items():
        if team_id not in nhl_teams.FRANCHISE_SUCCESSORS:
            divisions.setdefault(division, []).append(team_id)
    return divisions


def conference_of(division: str) -> str:
    return division.split(" ", 1)[0]


def _rating(ratings: dict[str, float], team_id: str) -> float:
    return ratings.get(nhl_teams.franchise_id(team_id), DEFAULT_STARTING_RATING)


def game_probability(home_id: str, away_id: str, ratings: dict[str, float], home_advantage: float = ELO_HOME_ADVANTAGE) -> float:
    """The home team's chance of winning one game."""
    return expected_score(_rating(ratings, home_id), _rating(ratings, away_id), home_advantage)


def series_win_probability(home_probability: float, away_probability: float, wins_a: int = 0, wins_b: int = 0) -> float:
    """Team A's chance of winning a best-of-seven it leads wins_a-wins_b,
    as the higher seed. home_probability/away_probability are A's chance
    in a game it hosts and in one it visits."""
    if wins_a >= SERIES_WINS:
        return 1.0
    if wins_b >= SERIES_WINS:
        return 0.0
    p = home_probability if HIGHER_SEED_HOME[wins_a + wins_b] else away_probability
    return (
        p * series_win_probability(home_probability, away_probability, wins_a + 1, wins_b)
        + (1 - p) * series_win_probability(home_probability, away_probability, wins_a, wins_b + 1)
    )


def predicted_series_score(home_probability: float, away_probability: float) -> tuple[int, int]:
    """The likeliest final series score, (higher seed wins, lower seed
    wins), given who is favoured to take it."""
    favoured = series_win_probability(home_probability, away_probability) >= 0.5
    best_score, best_probability = (SERIES_WINS, 0), -1.0
    for loser_wins in range(SERIES_WINS):
        probability = _exact_score_probability(home_probability, away_probability, favoured, loser_wins)
        if probability > best_probability:
            best_score = (SERIES_WINS, loser_wins) if favoured else (loser_wins, SERIES_WINS)
            best_probability = probability
    return best_score


def _exact_score_probability(home_probability: float, away_probability: float, a_wins_series: bool, loser_wins: int) -> float:
    """Chance the series ends 4-loser_wins for A (or for B)."""
    def walk(wins_a: int, wins_b: int) -> float:
        target = (SERIES_WINS, loser_wins) if a_wins_series else (loser_wins, SERIES_WINS)
        if (wins_a, wins_b) == target:
            return 1.0
        if wins_a >= SERIES_WINS or wins_b >= SERIES_WINS:
            return 0.0
        p = home_probability if HIGHER_SEED_HOME[wins_a + wins_b] else away_probability
        return p * walk(wins_a + 1, wins_b) + (1 - p) * walk(wins_a, wins_b + 1)
    return walk(0, 0)


def _standings_key(team_id: str, points: dict, regulation_wins: dict, wins: dict):
    return (points.get(team_id, 0), regulation_wins.get(team_id, 0), wins.get(team_id, 0))


def conference_fields(points: dict, regulation_wins: dict, wins: dict, tiebreak=None) -> dict[str, dict]:
    """Each conference's playoff field from a set of final standings:
    {conference: {"division_winners": [...], "brackets": [[d1, wildcard,
    d2, d3], [d1, wildcard, d2, d3]], "seeds": {team: conference rank}}}.
    The first bracket belongs to the better division winner, who draws the
    second wildcard. `tiebreak(team_id)` orders teams still level after
    points, regulation wins and wins."""
    def key(team_id: str):
        return (*_standings_key(team_id, points, regulation_wins, wins), tiebreak(team_id) if tiebreak else 0)

    by_conference: dict[str, list[list[str]]] = {}
    for division, teams in teams_by_division().items():
        by_conference.setdefault(conference_of(division), []).append(sorted(teams, key=key, reverse=True))
    return {conference: _conference_field(divisions, key) for conference, divisions in by_conference.items()}


def _conference_field(divisions: list[list[str]], key) -> dict:
    """One conference's field from its divisions, each already in standings order."""
    qualified = [team for teams in divisions for team in teams[:DIVISION_SEEDS]]
    rest = sorted((team for teams in divisions for team in teams[DIVISION_SEEDS:]), key=key, reverse=True)
    wildcards = rest[:WILDCARDS]
    # Best division winner first; it draws the last wildcard.
    ordered = sorted(divisions, key=lambda teams: key(teams[0]), reverse=True)
    draws = wildcards[::-1] + [None] * len(ordered)
    ranked = sorted(qualified + wildcards, key=key, reverse=True)
    return {
        "division_winners": [teams[0] for teams in divisions],
        "brackets": [[teams[0], wildcard, teams[1], teams[2]] for teams, wildcard in zip(ordered, draws)],
        "seeds": {team: rank for rank, team in enumerate(ranked, start=1)},
        "teams": set(qualified + wildcards),
    }


def _simulate_series(team_a: str, team_b: str, ratings: dict[str, float], home_advantage: float, rng: random.Random) -> str:
    """team_a is the higher seed."""
    home = game_probability(team_a, team_b, ratings, home_advantage)
    away = 1 - game_probability(team_b, team_a, ratings, home_advantage)
    wins_a = wins_b = 0
    while wins_a < SERIES_WINS and wins_b < SERIES_WINS:
        if rng.random() < (home if HIGHER_SEED_HOME[wins_a + wins_b] else away):
            wins_a += 1
        else:
            wins_b += 1
    return team_a if wins_a == SERIES_WINS else team_b


def _higher_seed_first(team_a: str, team_b: str, rank: dict[str, tuple]) -> tuple[str, str]:
    return (team_a, team_b) if rank[team_a] >= rank[team_b] else (team_b, team_a)


def _simulate_playoffs(fields: dict[str, dict], rank: dict[str, tuple], ratings: dict, home_advantage: float, rng: random.Random) -> str:
    finalists = []
    for field in fields.values():
        division_champions = []
        for d1, wildcard, d2, d3 in field["brackets"]:
            first = _simulate_series(d1, wildcard, ratings, home_advantage, rng) if wildcard else d1
            second = _simulate_series(d2, d3, ratings, home_advantage, rng)
            division_champions.append(_simulate_series(*_higher_seed_first(first, second, rank), ratings, home_advantage, rng))
        finalists.append(_simulate_series(*_higher_seed_first(*division_champions, rank), ratings, home_advantage, rng))
    return _simulate_series(*_higher_seed_first(*finalists, rank), ratings, home_advantage, rng)


def _play_out_schedule(current: dict[str, dict], remaining_games: list, probabilities: list[float], rng: random.Random) -> dict[str, dict]:
    """One simulated end-of-season record, starting from `current`:
    {"wins", "regulation_losses", "overtime_losses", "regulation_wins"}."""
    record = {name: dict(counts) for name, counts in current.items()}

    def credit(name: str, team: str) -> None:
        record[name][team] = record[name].get(team, 0) + 1

    for (home, away), probability in zip(remaining_games, probabilities):
        winner, loser = (home, away) if rng.random() < probability else (away, home)
        credit("wins", winner)
        if rng.random() < OVERTIME_RATE:
            credit("overtime_losses", loser)
        else:
            credit("regulation_losses", loser)
            credit("regulation_wins", winner)
    return record


def _tally(totals: dict[str, dict], record: dict[str, dict], fields: dict[str, dict], champion: str) -> None:
    """Adds one simulated season to the running totals."""
    for team, total in totals.items():
        for name in ("wins", "regulation_losses", "overtime_losses"):
            total[name] += record[name].get(team, 0)
    for field in fields.values():
        for team in field["division_winners"]:
            totals[team]["division"] += 1
        for team in field["teams"]:
            totals[team]["playoff"] += 1
    totals[champion]["champion"] += 1


def simulate_season(
    wins: dict[str, int], losses: dict[str, int], overtime_losses: dict[str, int], regulation_wins: dict[str, int],
    remaining_games: list[tuple[str, str]], ratings: dict[str, float], *,
    home_advantage: float = ELO_HOME_ADVANTAGE, simulations: int = DEFAULT_SIMULATIONS, seed: int | None = None,
) -> dict[str, dict]:
    """Plays out the rest of the regular season and the playoffs
    `simulations` times. remaining_games: (home_id, away_id) pairs.
    losses are regulation losses only. Returns, per current team:
    projected_wins, projected_losses (regulation and overtime together),
    projected_overtime_losses, projected_points, and the
    division_winner/playoff/championship probabilities.

    `seed` is for tests only."""
    rng = random.Random(seed)
    teams = [team for division in teams_by_division().values() for team in division]
    probabilities = [game_probability(home, away, ratings, home_advantage) for home, away in remaining_games]
    totals = {team: {"wins": 0, "regulation_losses": 0, "overtime_losses": 0, "division": 0, "playoff": 0, "champion": 0} for team in teams}

    current = {"wins": wins, "regulation_losses": losses, "overtime_losses": overtime_losses, "regulation_wins": regulation_wins}
    for _ in range(simulations):
        record = _play_out_schedule(current, remaining_games, probabilities, rng)
        points = {team: 2 * record["wins"].get(team, 0) + record["overtime_losses"].get(team, 0) for team in teams}
        coin = {team: rng.random() for team in teams}
        fields = conference_fields(points, record["regulation_wins"], record["wins"], coin.get)
        rank = {team: (*_standings_key(team, points, record["regulation_wins"], record["wins"]), coin[team]) for team in teams}
        champion = _simulate_playoffs(fields, rank, ratings, home_advantage, rng)
        _tally(totals, record, fields, champion)

    return {
        team: {
            "projected_wins": total["wins"] / simulations,
            "projected_losses": (total["regulation_losses"] + total["overtime_losses"]) / simulations,
            "projected_overtime_losses": total["overtime_losses"] / simulations,
            "projected_points": (2 * total["wins"] + total["overtime_losses"]) / simulations,
            "division_winner_probability": total["division"] / simulations,
            "playoff_probability": total["playoff"] / simulations,
            "championship_probability": total["champion"] / simulations,
        }
        for team, total in totals.items()
    }


def project_matchup(team_a: str | None, team_b: str | None, seeds: dict[str, int], ratings: dict, home_advantage: float) -> dict:
    """One projected series row; team_a is the higher seed. A side still
    undetermined (no wildcard yet) advances the other."""
    if team_a is None or team_b is None:
        known = team_a or team_b
        return {
            "team_a": team_a, "team_b": team_b, "seed_a": seeds.get(team_a), "seed_b": seeds.get(team_b),
            "status": "projected", "predicted_winner": known, "win_probability": 1.0 if known else None,
        }
    home = game_probability(team_a, team_b, ratings, home_advantage)
    away = 1 - game_probability(team_b, team_a, ratings, home_advantage)
    probability_a = series_win_probability(home, away)
    predicted_a, predicted_b = predicted_series_score(home, away)
    a_wins = probability_a >= 0.5
    return {
        "team_a": team_a, "team_b": team_b, "seed_a": seeds.get(team_a), "seed_b": seeds.get(team_b),
        "status": "projected",
        "predicted_winner": team_a if a_wins else team_b,
        "win_probability": probability_a if a_wins else 1 - probability_a,
        "predicted_wins_a": predicted_a, "predicted_wins_b": predicted_b,
    }


def project_bracket(
    points: dict[str, float], regulation_wins: dict[str, float], wins: dict[str, float], ratings: dict[str, float],
    home_advantage: float = ELO_HOME_ADVANTAGE,
) -> dict:
    """The single most likely bracket if the season ended on these
    standings: {"conferences": {name: [round, round, round]}, "finals":
    matchup, "champion": team}. Each round is {"round", "matchups"}."""
    fields = conference_fields(points, regulation_wins, wins)
    rank = {team: _standings_key(team, points, regulation_wins, wins) for field in fields.values() for team in field["teams"]}

    def play(team_a: str | None, team_b: str | None, seeds: dict[str, int]) -> dict:
        if team_a is not None and team_b is not None:
            team_a, team_b = _higher_seed_first(team_a, team_b, rank)
        return project_matchup(team_a, team_b, seeds, ratings, home_advantage)

    conferences, finalists, all_seeds = {}, [], {}
    for conference, field in sorted(fields.items()):
        seeds = field["seeds"]
        all_seeds.update(seeds)
        first_round, second_round = [], []
        for d1, wildcard, d2, d3 in field["brackets"]:
            top, bottom = play(d1, wildcard, seeds), play(d2, d3, seeds)
            first_round += [top, bottom]
            second_round.append(play(top["predicted_winner"], bottom["predicted_winner"], seeds))
        final = play(second_round[0]["predicted_winner"], second_round[1]["predicted_winner"], seeds)
        conferences[conference] = [
            {"round": ROUND_NAMES[0], "matchups": first_round},
            {"round": ROUND_NAMES[1], "matchups": second_round},
            {"round": ROUND_NAMES[2], "matchups": [final]},
        ]
        finalists.append(final["predicted_winner"])

    finals = play(finalists[0], finalists[1], all_seeds)
    return {"conferences": conferences, "finals": {**finals, "round": FINAL_ROUND}, "champion": finals["predicted_winner"]}


project_leaderboard = season_projection_common.project_leaderboard
