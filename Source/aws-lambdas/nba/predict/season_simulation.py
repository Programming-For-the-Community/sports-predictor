"""
Regular-season Monte Carlo simulation (win totals, play-in odds, playoff
field, championship pick) and NBA Cup (in-season tournament) simulation
for the NBA Season tab. Pure functions -- no AWS/storage access.

Uses Elo win probability (library.features.common.expected_score) instead
of the full trained model, since reconstructing a full feature vector for
thousands of Monte Carlo paths through the rest of a season is
intractable for on-request compute.

NBA seeds strictly by conference record, with no divisions in seeding
(see _seed_conference); a play-in round (seeds 7-10 per conference) sits
between the regular season and the 16-team bracket (see simulate_season
for the full seed flow, and _simulate_play_in/_simulate_bracket for the
two distinct brackets that make up a conference's path to the
championship).

Playoff/play-in seeding is simplified: ties broken by point differential
then arbitrarily -- the real NBA's tiebreaker rules (head-to-head,
division record, etc.) aren't implemented.

Every real NBA playoff round except Play-In (Conference Quarterfinals,
Conference Semifinals, Conference Finals, NBA Finals) is a best-of-7
series, not a single game; Play-In stays single-elimination throughout,
including its own 7-vs-8 game. series_win_probability below is what makes
a bracket slot series-aware; season_projection.py's own
_resolve_series_matchup is the caller that threads a live series' real
win/loss record through it.

NBA Cup (in-season tournament) simulation (see simulate_cup) is a
separate, optional layer: it needs real group membership from
library.features.nba_cup_groups.CUP_GROUPS, a hand-maintained table, and
returns None for any season not yet added there rather than guessing at
group assignments.
"""
import random

from library.features.common import DEFAULT_HOME_ADVANTAGE, DEFAULT_K_FACTOR, DEFAULT_STARTING_RATING, expected_score
from library.features.nba_cup_groups import CUP_GROUPS, cup_group_for_team
from library.features.nba_teams import TEAM_DIVISIONS

DEFAULT_SIMULATIONS = 2000

# Seeds 1-6 per conference go directly to the 16-team playoff bracket;
# seeds 7-10 enter the play-in tournament (see _simulate_play_in) for the
# conference's final two bracket spots; seeds 11-15 are eliminated.
DIRECT_PLAYOFF_SEEDS = 6
PLAY_IN_FIELD_SIZE = 10


def _conference(division: str) -> str:
    return division.split()[0]  # "Eastern Atlantic" -> "Eastern"


def _teams_by_conference() -> dict[str, list[str]]:
    """{"Eastern": [team_id, ...], "Western": [...]} -- all 15 teams per
    conference. NBA seeding doesn't group by division (see
    _seed_conference)."""
    result: dict[str, list[str]] = {}
    for team_id, division in TEAM_DIVISIONS.items():
        result.setdefault(_conference(division), []).append(team_id)
    return result


def _teams_by_division() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for team_id, division in TEAM_DIVISIONS.items():
        result.setdefault(division, []).append(team_id)
    return result


def _seed_conference(teams: list[str], wins: dict[str, int], point_differential: dict[str, int]) -> list[str]:
    """Seeds 1..len(teams) for one conference, best record first, by
    conference win total (no guaranteed division-winner seed). Ties broken
    by point differential, then arbitrarily (list order) if still tied."""
    def sort_key(team_id: str) -> tuple[int, int]:
        return (wins.get(team_id, 0), point_differential.get(team_id, 0))
    return sorted(teams, key=sort_key, reverse=True)


def _play(team_a: str, team_b: str, seed_rank: dict[str, int], ratings: dict[str, float],
          home_advantage: float, rng: random.Random) -> str:
    """One simulated game between two already-seeded teams -- the lower
    (numerically, i.e. better) seed always hosts."""
    home, away = (team_a, team_b) if seed_rank[team_a] < seed_rank[team_b] else (team_b, team_a)
    home_rating = ratings.get(home, DEFAULT_STARTING_RATING)
    away_rating = ratings.get(away, DEFAULT_STARTING_RATING)
    home_win_probability = expected_score(home_rating, away_rating, home_advantage)
    return home if rng.random() < home_win_probability else away


def _simulate_play_in(seed_7: str, seed_8: str, seed_9: str, seed_10: str, ratings: dict[str, float],
                       home_advantage: float, rng: random.Random) -> tuple[str, str]:
    """Standard NBA play-in tournament: Game 1 (7 vs 8, hosted by 7) --
    the winner claims the conference's final 7 seed outright. Game 2 (9
    vs 10, hosted by 9) -- the LOSER is eliminated; the winner advances
    to Game 3 (Game 1's loser vs Game 2's winner, hosted by Game 1's
    loser, since it's the better-seeded team of the two) for the
    conference's 8 seed. Returns (final_7_seed, final_8_seed)."""
    seed_rank = {seed_7: 0, seed_8: 1, seed_9: 2, seed_10: 3}

    def play(a: str, b: str) -> str:
        return _play(a, b, seed_rank, ratings, home_advantage, rng)

    game1_winner = play(seed_7, seed_8)
    game1_loser = seed_8 if game1_winner == seed_7 else seed_7
    game2_winner = play(seed_9, seed_10)
    final_8_seed = play(game1_loser, game2_winner)
    return game1_winner, final_8_seed


def _simulate_bracket(seeds: list[str], ratings: dict[str, float], home_advantage: float, rng: random.Random) -> str:
    """Fixed 8-team single-elimination bracket, seeds 1-8 (index 0-7), no
    reseeding between rounds: 1v8, 4v5, 3v6, 2v7 in round one; (1v8)/(4v5)
    winners meet in one conference semifinal, (3v6)/(2v7) winners in the
    other; those two winners play for the conference championship. The
    higher (numerically lower) remaining seed always hosts."""
    seed_rank = {team_id: rank for rank, team_id in enumerate(seeds)}

    def play(a: str, b: str) -> str:
        return _play(a, b, seed_rank, ratings, home_advantage, rng)

    one, two, three, four, five, six, seven, eight = seeds
    round1 = [play(one, eight), play(four, five), play(three, six), play(two, seven)]
    semi1 = play(round1[0], round1[1])
    semi2 = play(round1[2], round1[3])
    return play(semi1, semi2)


def project_matchup(
    team_a: str, team_b: str, seed_a: int | None, seed_b: int | None,
    ratings: dict[str, float], home_advantage: float,
) -> dict:
    """Deterministic single-matchup resolution -- no RNG, picks whichever
    side has >= 50% win probability. When both seeds are known, the
    better (numerically lower) seed always hosts; if either is None,
    team_a/team_b keep their given order and home_advantage decides the
    framing (see project_finals, which pre-picks the real home side and
    passes no seeds).

    Returns {"team_a", "team_b", "seed_a", "seed_b", "predicted_winner",
    "win_probability"} -- win_probability is always the winner's own
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


def series_win_probability(game_probability: float, wins_a: int, wins_b: int) -> float:
    """P(team A wins a best-of-7 series | already leads wins_a-wins_b),
    with every remaining game independently won by A at game_probability
    -- the standard recursive series-win-probability formula. wins_a=
    wins_b=0 (a series that hasn't started yet) reduces to the pure
    binomial best-of-7 formula; a live in-progress record (e.g. 3-1)
    narrows the remaining uncertainty to just the games left to play.
    Recursion depth is capped at 7 (a team can't lead a series by more
    than 4 wins), so this needs no memoization."""
    if wins_a >= 4:
        return 1.0
    if wins_b >= 4:
        return 0.0
    return (
        game_probability * series_win_probability(game_probability, wins_a + 1, wins_b)
        + (1 - game_probability) * series_win_probability(game_probability, wins_a, wins_b + 1)
    )


def predicted_series_score(game_probability: float, wins_a: int = 0, wins_b: int = 0) -> tuple[int, int]:
    """The single most likely final (wins_a, wins_b) a best-of-7 series
    ends at, given a fixed per-game win probability for team A and the
    series' current record. Deterministic (no RNG) -- picks the
    highest-probability terminal state out of every way the series can
    end (at most 2*C(6,3)=40 terminal states from a 0-0 start). wins_a=
    wins_b=0 predicts a series that hasn't started; a live in-progress
    record (e.g. 3-1) predicts only the games left to play."""
    memo: dict[tuple[int, int], dict[tuple[int, int], float]] = {}

    def terminal_probabilities(a: int, b: int) -> dict[tuple[int, int], float]:
        if a >= 4 or b >= 4:
            return {(a, b): 1.0}
        if (a, b) in memo:
            return memo[(a, b)]
        combined: dict[tuple[int, int], float] = {}
        for state, probability in terminal_probabilities(a + 1, b).items():
            combined[state] = combined.get(state, 0.0) + probability * game_probability
        for state, probability in terminal_probabilities(a, b + 1).items():
            combined[state] = combined.get(state, 0.0) + probability * (1 - game_probability)
        memo[(a, b)] = combined
        return combined

    probabilities = terminal_probabilities(wins_a, wins_b)
    return max(probabilities, key=probabilities.get)


def project_play_in(
    seed_7: str, seed_8: str, seed_9: str, seed_10: str, ratings: dict[str, float], home_advantage: float,
) -> dict:
    """Deterministic version of the play-in tournament (see
    _simulate_play_in for the game format), no RNG. Returns {"games":
    [game3, game2, game1], "final_7_seed": team_id, "final_8_seed":
    team_id} -- game3 first and game1 last so the list reads top-to-bottom
    in the order the bracket UI's Conference Quarterfinals column expects:
    game3's winner (final_8_seed) feeds the top Quarterfinal (1 vs 8) and
    game1's winner (final_7_seed) feeds the bottom one (2 vs 7), with
    game2 (which only feeds game3) kept next to game3."""
    seed_number = {seed_7: 7, seed_8: 8, seed_9: 9, seed_10: 10}
    game1 = project_matchup(seed_7, seed_8, 7, 8, ratings, home_advantage)
    game1_winner = game1["predicted_winner"]
    game1_loser = seed_8 if game1_winner == seed_7 else seed_7
    game2 = project_matchup(seed_9, seed_10, 9, 10, ratings, home_advantage)
    game2_winner = game2["predicted_winner"]
    game3 = project_matchup(
        game1_loser, game2_winner, seed_number[game1_loser], seed_number[game2_winner], ratings, home_advantage,
    )
    return {"games": [game3, game2, game1], "final_7_seed": game1_winner, "final_8_seed": game3["predicted_winner"]}


def project_bracket(seeds: list[str], ratings: dict[str, float], home_advantage: float = DEFAULT_HOME_ADVANTAGE) -> dict:
    """Deterministic version of the fixed, no-reseeding 8-team bracket
    (1v8/4v5/3v6/2v7 in the first round; those winners meet in the two
    conference semifinals; those winners play for the conference
    championship). `seeds` is the already-resolved 8-team field (direct
    seeds 1-6 plus the play-in's own final_7_seed/final_8_seed -- see
    project_conference_bracket below, which combines both stages). Picks
    the higher-win-probability side every matchup and returns the full
    round-by-round path instead of just a champion.

    Returns {"rounds": [{"round": "Conference Quarterfinals", "matchups": [...]},
    ...], "champion": team_id}.
    """
    seed_number = {team_id: rank + 1 for rank, team_id in enumerate(seeds)}
    one, two, three, four, five, six, seven, eight = seeds

    first_round_pairs = [(one, eight), (four, five), (three, six), (two, seven)]
    first_round = [
        project_matchup(a, b, seed_number[a], seed_number[b], ratings, home_advantage) for a, b in first_round_pairs
    ]
    r1w = [m["predicted_winner"] for m in first_round]

    semifinals = [
        project_matchup(r1w[0], r1w[1], seed_number[r1w[0]], seed_number[r1w[1]], ratings, home_advantage),
        project_matchup(r1w[2], r1w[3], seed_number[r1w[2]], seed_number[r1w[3]], ratings, home_advantage),
    ]
    sf1, sf2 = (m["predicted_winner"] for m in semifinals)

    championship = project_matchup(sf1, sf2, seed_number[sf1], seed_number[sf2], ratings, home_advantage)

    return {
        "rounds": [
            {"round": "Conference Quarterfinals", "matchups": first_round},
            {"round": "Conference Semifinals", "matchups": semifinals},
            {"round": "Conference Finals", "matchups": [championship]},
        ],
        "champion": championship["predicted_winner"],
    }


def project_conference_bracket(
    direct_seeds: list[str], seed_7: str, seed_8: str, seed_9: str, seed_10: str,
    ratings: dict[str, float], home_advantage: float = DEFAULT_HOME_ADVANTAGE,
) -> dict:
    """Combines project_play_in and project_bracket into one conference's
    full postseason path. direct_seeds is seeds 1-6, already ordered.

    Play-In renders as two rounds, not one -- game 3 (the elimination
    game) isn't played in parallel with games 1/2, it's built FROM their
    results (game 1's loser vs game 2's winner -- see project_play_in),
    so it belongs one round later, not beside them. Within that first
    round, game2 goes first and game1 last: game1's winner never appears
    in the elimination game -- it advances straight through to the
    Quarterfinals' own (2 vs 7) pairing -- so putting game1 last keeps it
    next to that pairing's position two rounds later."""
    play_in = project_play_in(seed_7, seed_8, seed_9, seed_10, ratings, home_advantage)
    full_seeds = direct_seeds + [play_in["final_7_seed"], play_in["final_8_seed"]]
    bracket = project_bracket(full_seeds, ratings, home_advantage)
    game3, game2, game1 = play_in["games"]
    return {
        "rounds": [
            {"round": "Play-In", "matchups": [game2, game1]},
            {"round": "Play-In Elimination", "matchups": [game3]},
            *bracket["rounds"],
        ],
        "champion": bracket["champion"],
    }


def project_finals(
    champion_a: str, champion_b: str, wins: dict[str, int], point_differential: dict[str, int],
    ratings: dict[str, float], home_advantage: float = DEFAULT_HOME_ADVANTAGE,
) -> dict:
    """Deterministic NBA Finals matchup -- home-court goes to the better
    regular-season record, point differential as tiebreak. A team_a win on
    a full tie is a stable, arbitrary pick, not a coinflip."""
    record_a = (wins.get(champion_a, 0), point_differential.get(champion_a, 0))
    record_b = (wins.get(champion_b, 0), point_differential.get(champion_b, 0))
    home, away = (champion_a, champion_b) if record_a >= record_b else (champion_b, champion_a)
    return project_matchup(home, away, None, None, ratings, home_advantage)


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

    Returns {team_id: {"projected_wins": float, "projected_losses":
    float, "division_winner_probability": float, "play_in_probability":
    float, "playoff_probability": float, "championship_probability":
    float}}.

    division_winner_probability is informational only -- the NBA hasn't
    tied any playoff-seeding benefit to division titles since 2015-16 (see
    _seed_conference), so this reports "best record in own division", not
    a real reward.

    play_in_probability is the fraction of simulated paths where the
    team finishes seeds 7-10 (must play at least one play-in game) --
    not the same as playoff_probability, which tracks whether the team
    ultimately reaches the 16-team bracket regardless of path (top-6
    direct, or won through the play-in as the final 7/8 seed). A team can
    be, and often is, high in both at once.

    No projected_ties -- home_won below is a strict win/loss draw.
    Simulated rating updates use a plain K-factor adjustment, no
    margin-of-victory scaling.
    """
    rng = rng or random.Random()
    # Always the full real league (TEAM_DIVISIONS' own 30 teams), not just
    # whoever appears in current_wins/remaining_games -- _seed_conference
    # needs every team in a conference to seed it correctly, and every
    # team needs an entry in every aggregate dict even if it wasn't
    # explicitly passed in (e.g. a team with no games left still needs a
    # projection).
    teams = set(TEAM_DIVISIONS)

    win_totals = {team_id: 0.0 for team_id in teams}
    loss_totals = {team_id: 0.0 for team_id in teams}
    division_titles = {team_id: 0 for team_id in teams}
    play_in_berths = {team_id: 0 for team_id in teams}
    playoff_berths = {team_id: 0 for team_id in teams}
    championships = {team_id: 0 for team_id in teams}

    conferences = _teams_by_conference()
    divisions = _teams_by_division()

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

        for division_teams in divisions.values():
            winner = max(division_teams, key=lambda t: (wins.get(t, 0), point_differential.get(t, 0)))
            division_titles[winner] += 1

        conference_champions = []
        for conference_teams in conferences.values():
            seeds = _seed_conference(conference_teams, wins, point_differential)
            direct_seeds = seeds[:DIRECT_PLAYOFF_SEEDS]
            seed_7, seed_8, seed_9, seed_10 = seeds[6:PLAY_IN_FIELD_SIZE]

            for team_id in (seed_7, seed_8, seed_9, seed_10):
                play_in_berths[team_id] += 1

            final_7, final_8 = _simulate_play_in(seed_7, seed_8, seed_9, seed_10, ratings, home_advantage, rng)

            playoff_seeds = direct_seeds + [final_7, final_8]
            for team_id in playoff_seeds:
                playoff_berths[team_id] += 1

            champion = _simulate_bracket(playoff_seeds, ratings, home_advantage, rng)
            conference_champions.append((champion, wins[champion], point_differential.get(champion, 0)))

        # Finals -- home-court to the better regular-season record, point
        # differential as tiebreak, an even-odds coinflip only if still
        # tied on both.
        (champ_a, wins_a, pd_a), (champ_b, wins_b, pd_b) = conference_champions
        record_a, record_b = (wins_a, pd_a), (wins_b, pd_b)
        if record_a == record_b:
            home, away = (champ_a, champ_b) if rng.random() < 0.5 else (champ_b, champ_a)
        elif record_a > record_b:
            home, away = champ_a, champ_b
        else:
            home, away = champ_b, champ_a
        home_rating = ratings.get(home, DEFAULT_STARTING_RATING)
        away_rating = ratings.get(away, DEFAULT_STARTING_RATING)
        home_win_probability = expected_score(home_rating, away_rating, home_advantage)
        champion = home if rng.random() < home_win_probability else away
        championships[champion] += 1

    return {
        team_id: {
            "projected_wins": win_totals[team_id] / simulations,
            "projected_losses": loss_totals[team_id] / simulations,
            "division_winner_probability": division_titles[team_id] / simulations,
            "play_in_probability": play_in_berths[team_id] / simulations,
            "playoff_probability": playoff_berths[team_id] / simulations,
            "championship_probability": championships[team_id] / simulations,
        }
        for team_id in teams
    }


def simulate_cup(
    season: int | None,
    cup_wins: dict[str, int],
    cup_losses: dict[str, int],
    remaining_cup_games: list[tuple[str, str]],
    current_ratings: dict[str, float],
    simulations: int = DEFAULT_SIMULATIONS,
    home_advantage: float = DEFAULT_HOME_ADVANTAGE,
    k_factor: float = DEFAULT_K_FACTOR,
    rng: random.Random | None = None,
) -> dict[str, dict] | None:
    """NBA Cup (in-season tournament) simulation -- a separate mini-season
    layered on top of a subset of the same remaining_games simulate_season
    also walks. Returns None if `season` isn't in CUP_GROUPS yet -- there's
    nothing real to simulate without real group membership.

    Otherwise returns {team_id: {"group": "Eastern A", "group_wins": int,
    "group_losses": int, "group_winner_probability": float,
    "knockout_probability": float, "cup_finalist_probability": float,
    "champion_probability": float}} for every one of the 30 teams in
    CUP_GROUPS[season], whether or not cup_wins/cup_losses mentions them
    yet (a team with zero group games played so far still has a real
    group and a real, if early, probability).

    Format: 3 groups of 5 teams per conference. The best-record team in
    each group (group_wins/group_losses, ties broken by group wins then
    losses -- simplified, real point-differential tiebreaks within group
    games aren't implemented) plus the single best remaining
    (non-group-winner) record in that conference (the "wildcard") advance
    -- 4 teams per conference. Knockout: fixed single-elimination
    semifinal + final within each conference (1v4, 2v3 by group-stage
    record, higher seed hosts); the two conference champions meet in the
    NBA Cup Championship at a neutral site (modeled at even odds, no real
    home side).
    """
    groups = CUP_GROUPS.get(season) if season is not None else None
    if not groups:
        return None

    rng = rng or random.Random()
    all_teams = [team_id for conference in groups.values() for team_ids in conference.values() for team_id in team_ids]

    group_winner_totals = {team_id: 0 for team_id in all_teams}
    knockout_totals = {team_id: 0 for team_id in all_teams}
    finalist_totals = {team_id: 0 for team_id in all_teams}
    champion_totals = {team_id: 0 for team_id in all_teams}

    for _ in range(simulations):
        wins = {team_id: cup_wins.get(team_id, 0) for team_id in all_teams}
        losses = {team_id: cup_losses.get(team_id, 0) for team_id in all_teams}
        ratings = dict(current_ratings)

        for home_id, away_id in remaining_cup_games:
            if home_id not in wins or away_id not in wins:
                continue  # not a real franchise in this season's groups -- skip defensively
            home_rating = ratings.get(home_id, DEFAULT_STARTING_RATING)
            away_rating = ratings.get(away_id, DEFAULT_STARTING_RATING)
            home_win_probability = expected_score(home_rating, away_rating, home_advantage)
            home_won = rng.random() < home_win_probability
            wins[home_id if home_won else away_id] += 1
            losses[away_id if home_won else home_id] += 1
            actual_home = 1.0 if home_won else 0.0
            ratings[home_id] = home_rating + k_factor * (actual_home - home_win_probability)
            ratings[away_id] = away_rating + k_factor * ((1 - actual_home) - (1 - home_win_probability))

        def record_key(team_id: str) -> tuple[int, int]:
            return (wins.get(team_id, 0), -losses.get(team_id, 0))

        knockout_field_by_conference: dict[str, list[str]] = {}
        for conference, conference_groups in groups.items():
            group_winners = []
            all_conference_teams = []
            for team_ids in conference_groups.values():
                winner = max(team_ids, key=record_key)
                group_winner_totals[winner] += 1
                group_winners.append(winner)
                all_conference_teams.extend(team_ids)

            wildcard_pool = [t for t in all_conference_teams if t not in group_winners]
            wildcard = max(wildcard_pool, key=record_key)

            field = sorted(group_winners, key=record_key, reverse=True) + [wildcard]
            knockout_field_by_conference[conference] = field
            for team_id in field:
                knockout_totals[team_id] += 1

        conference_champions = []
        for field in knockout_field_by_conference.values():
            seed_rank = {team_id: rank for rank, team_id in enumerate(field)}

            def play(a: str, b: str) -> str:
                return _play(a, b, seed_rank, ratings, home_advantage, rng)

            one, two, three, four = field
            semi1 = play(one, four)
            semi2 = play(two, three)
            finalist = play(semi1, semi2)
            finalist_totals[finalist] += 1
            conference_champions.append(finalist)

        # Championship game -- neutral site, modeled at even odds (no
        # real home side).
        champion = conference_champions[0] if rng.random() < 0.5 else conference_champions[1]
        champion_totals[champion] += 1

    return {
        team_id: {
            "group": cup_group_for_team(season, team_id),
            "group_wins": cup_wins.get(team_id, 0),
            "group_losses": cup_losses.get(team_id, 0),
            "group_winner_probability": group_winner_totals[team_id] / simulations,
            "knockout_probability": knockout_totals[team_id] / simulations,
            "cup_finalist_probability": finalist_totals[team_id] / simulations,
            "champion_probability": champion_totals[team_id] / simulations,
        }
        for team_id in all_teams
    }


def project_cup_knockout_bracket(
    season: int | None, cup_wins: dict[str, int], cup_losses: dict[str, int], ratings: dict[str, float],
    home_advantage: float = 0.0,
) -> dict | None:
    """Deterministic version of simulate_cup's knockout-stage logic (3
    group winners + 1 wildcard per conference, seeded by group_wins/
    losses -- mirrored here unchanged): semis (1v4/2v3) then a conference
    final, both conference finalists then meet in the Cup Championship.
    None if `season` isn't in CUP_GROUPS yet, same as simulate_cup.
    Neutral site throughout (home_advantage=0.0 default).

    Projected only -- unlike the main playoff bracket's own
    season_projection.py _bracket_payload, this does not attempt
    real-vs-actual reconciliation for Cup knockout games.
    """
    groups = CUP_GROUPS.get(season) if season is not None else None
    if not groups:
        return None

    def record_key(team_id: str) -> tuple[int, int]:
        return (cup_wins.get(team_id, 0), -cup_losses.get(team_id, 0))

    # project_matchup's own return shape has no "status" key; the frontend's
    # BracketMatchup.fromJson requires one as a non-nullable field on every
    # matchup regardless of bracket type.
    def projected(matchup: dict) -> dict:
        return {**matchup, "status": "projected"}

    conference_rounds: dict[str, list[dict]] = {}
    conference_finalists: dict[str, str] = {}
    for conference, conference_groups in groups.items():
        group_winners = [max(team_ids, key=record_key) for team_ids in conference_groups.values()]
        all_conference_teams = [team_id for team_ids in conference_groups.values() for team_id in team_ids]
        wildcard_pool = [t for t in all_conference_teams if t not in group_winners]
        wildcard = max(wildcard_pool, key=record_key)
        field = sorted(group_winners, key=record_key, reverse=True) + [wildcard]
        seed_number = {team_id: rank + 1 for rank, team_id in enumerate(field)}
        one, two, three, four = field

        semi1 = projected(project_matchup(one, four, seed_number[one], seed_number[four], ratings, home_advantage))
        semi2 = projected(project_matchup(two, three, seed_number[two], seed_number[three], ratings, home_advantage))
        finalist_a, finalist_b = semi1["predicted_winner"], semi2["predicted_winner"]
        conference_final = projected(project_matchup(
            finalist_a, finalist_b, seed_number.get(finalist_a), seed_number.get(finalist_b), ratings, home_advantage,
        ))

        conference_rounds[conference] = [
            {"round": "Semifinals", "matchups": [semi1, semi2]},
            {"round": "Conference Final", "matchups": [conference_final]},
        ]
        conference_finalists[conference] = conference_final["predicted_winner"]

    (conference_a, finalist_a), (conference_b, finalist_b) = conference_finalists.items()
    championship = projected(project_matchup(finalist_a, finalist_b, None, None, ratings, 0.0))

    return {
        "conferences": conference_rounds,
        "championship": championship,
        "champion": championship["predicted_winner"],
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
    remaining game) -- not a per-opponent simulation. Returns the top_n by
    projected total, descending.
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
