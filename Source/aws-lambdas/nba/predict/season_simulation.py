"""
Regular-season Monte Carlo simulation (win totals, play-in odds, playoff
field, championship pick) and NBA Cup (in-season tournament) simulation
for the NBA Season tab. Pure functions -- no AWS/storage access, same
testability philosophy as NFL's own season_simulation.py and
library/features/nba.py's own functions -- but lives here, not there,
since none of this runs at training time; it exists purely to answer a
serving-time question ("how does the rest of the season likely play out").

Uses Elo win probability (library.features.common.expected_score), NOT
the full trained model -- same reasoning as NFL's own module's docstring
(reconstructing a full feature vector for thousands of Monte Carlo paths
through the rest of a season is intractable for an on-request compute).

Playoff format genuinely differs from NFL's, not just a parameter tweak:
no divisions in seeding at all (NBA seeds strictly by conference record
since the 2015-16 season, unlike NFL's guaranteed division-winner top-4
seeds -- see _seed_conference) and a play-in round (seeds 7-10 per
conference) sits between the regular season and the 16-team bracket (see
simulate_season's own docstring for the full seed flow, and
_simulate_play_in/_simulate_bracket for the two distinct brackets that
make up a conference's path to the championship).

Playoff/play-in seeding is SIMPLIFIED, same disclaimer as NFL's own
module: ties broken by point differential then arbitrarily -- the real
NBA's tiebreaker rules (head-to-head, division record, etc.) aren't
implemented.

NBA Cup (in-season tournament) simulation -- see simulate_cup's own
docstring -- is a separate, optional layer: it needs real group
membership from library.features.nba_cup_groups.CUP_GROUPS, a hand-
maintained table (see that module's own docstring for why it can't be
fetched from ESPN's API), and returns None for any season not yet added
there rather than guessing at group assignments.
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
    conference, unlike NFL's own _divisions_by_conference (NBA seeding
    doesn't group by division at all, see _seed_conference)."""
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
    """Seeds 1..len(teams) for one conference, best record first -- the
    NBA has seeded strictly by conference win total since the 2015-16
    season, no guaranteed division-winner seed the way NFL's own
    _seed_conference implements. Ties broken by point differential, then
    arbitrarily (list order) if still tied."""
    def sort_key(team_id: str) -> tuple[int, int]:
        return (wins.get(team_id, 0), point_differential.get(team_id, 0))
    return sorted(teams, key=sort_key, reverse=True)


def _play(team_a: str, team_b: str, seed_rank: dict[str, int], ratings: dict[str, float],
          home_advantage: float, rng: random.Random) -> str:
    """One simulated game between two already-seeded teams -- the lower
    (numerically, i.e. better) seed always hosts, same convention as
    NFL's own bracket helper."""
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
    """Fixed 8-team single-elimination bracket, seeds 1-8 (index 0-7),
    NO reseeding between rounds -- unlike NFL's own _simulate_bracket,
    this matches the NBA's real, unchanging playoff format: 1v8, 4v5,
    3v6, 2v7 in round one; (1v8)/(4v5) winners meet in one conference
    semifinal, (3v6)/(2v7) winners in the other; those two winners play
    for the conference championship. The higher (numerically lower)
    remaining seed always hosts."""
    seed_rank = {team_id: rank for rank, team_id in enumerate(seeds)}

    def play(a: str, b: str) -> str:
        return _play(a, b, seed_rank, ratings, home_advantage, rng)

    one, two, three, four, five, six, seven, eight = seeds
    round1 = [play(one, eight), play(four, five), play(three, six), play(two, seven)]
    semi1 = play(round1[0], round1[1])
    semi2 = play(round1[2], round1[3])
    return play(semi1, semi2)


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

    division_winner_probability is INFORMATIONAL ONLY, unlike NFL's own
    field of the same name -- the NBA hasn't tied any playoff-seeding
    benefit to division titles since 2015-16 (see _seed_conference's own
    docstring), so this just reports "best record in own division", not
    a real reward.

    play_in_probability is the fraction of simulated paths where the
    team finishes seeds 7-10 (must play at least one play-in game) --
    NOT the same as playoff_probability, which tracks whether the team
    ultimately reaches the real 16-team bracket regardless of path (top-6
    direct, OR won through the play-in as the final 7/8 seed). A team can
    be, and often is, high in both at once.

    No projected_ties -- home_won below is a strict win/loss draw, the
    same simplification NFL's own module documents for margin-of-victory.
    Simulated rating updates use a plain K-factor adjustment, no
    margin-of-victory scaling, same reasoning as NFL's own module.
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

        # Finals -- home-court to the better regular-season record (the
        # real NBA rule, unlike NFL's own fixed-host Super Bowl), point
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
    """NBA Cup (in-season tournament) simulation -- a separate mini-
    season layered on top of (a subset of) the same remaining_games
    simulate_season also walks. Returns None if `season` isn't in
    CUP_GROUPS yet (see that module's own docstring) -- there's nothing
    real to simulate without real group membership, and this deliberately
    does not guess at group assignments.

    Otherwise returns {team_id: {"group": "Eastern A", "group_wins": int,
    "group_losses": int, "group_winner_probability": float,
    "knockout_probability": float, "cup_finalist_probability": float,
    "champion_probability": float}} for every one of the 30 teams in
    CUP_GROUPS[season], whether or not cup_wins/cup_losses mentions them
    yet (a team with zero group games played so far still has a real
    group and a real, if early, probability).

    Format: 3 groups of 5 teams per conference. The best-record team in
    each group (group_wins/group_losses only, ties broken by group wins
    then losses -- SIMPLIFIED, the real NBA Cup breaks ties by point
    differential within group games specifically, not implemented here)
    plus the single best remaining (non-group-winner) record in that
    conference (the "wildcard") advance -- 4 teams per conference.
    Knockout: fixed single-elimination semifinal + final within each
    conference (1v4, 2v3 by group-stage record, higher seed hosts); the
    two conference champions meet in the NBA Cup Championship at a
    neutral site (modeled at even odds, same simplification as
    simulate_season's own NBA Finals... no, as NFL's own Super Bowl,
    which has no real home side either).
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

        # Championship game -- neutral site, even odds, same "no real
        # home side" simplification as NFL's own Super Bowl.
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
    Identical to NFL's own project_leaderboard -- basketball's own
    per-sport season_projection.py needs the exact same formula, just fed
    NBA's own stat set.
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
