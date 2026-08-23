"""
NCAA MBB season simulation building blocks. Pure functions -- no AWS/
storage access, same discipline as NFL/NCAAFB/NBA's own season_simulation
modules.

Unlike every other sport onboarded so far, NCAA MBB needs TWO real
elimination-bracket features (conference tournaments -- one per
conference, field size 8 to ~18 -- and the 68-team March Madness field),
not one fixed-size bracket. NFL's reseeding, NCAAFB's hardcoded 12-team
CFP, and NBA's hardcoded 8-team playoff/4-team Cup bracket walkers don't
generalize to "N teams, N varies, byes needed for a non-power-of-2
field" -- so project_single_elim_bracket below is written once, generic
over field size, and reused for both features (the one deliberate
exception to this project's usual "duplicate per sport" convention,
since it's shared *within* one sport across two features, not across
sports -- see project-phase3-nba-ncaambb-plan memory's bracket-design
section).

Conference tournament seeding: within one conference, rank members by
conference-only record (wins then point differential) -- same simplified
tiebreak NCAAFB's own _conference_champion already accepts (no real
head-to-head/RPI tiebreakers). That conference bracket's own champion
(resolved by project_single_elim_bracket, or the real result once known
-- see season_projection.py's reconciliation layer) becomes that
conference's automatic bid into March Madness.

March Madness: len(conferences) automatic bids (real D1 realignment
changes this count -- NOT a hardcoded 32/68, see
library.http.ncaambb_core's resolve_conference_membership) plus the
best-ranked at-large teams, per the trained national-ranking model's
score_teams callable. First Four trims the 4 weakest auto bids and 4
weakest at-large teams to 64 (structurally identical glue to NBA's own
Play-In -- project_first_four's winners splice into their designated
Round-of-64 slot, reusing the frontend's existing skip-connector UI with
zero new frontend code). The 64-team field is then S-curve/snake-seeded
into 4 regions of 16 (no byes needed there); each region is its own
project_single_elim_bracket (home_advantage=0.0 -- every NCAA tournament
game is neutral-site); region champions meet at a Final Four, then a
Championship, both neutral-site. Region names are generic ("Region A"..
"Region D") -- this project doesn't have real geographic region-name
data, and the model doesn't need it to pick winners.

Monte Carlo (simulate_season): DEFAULT_SIMULATIONS is deliberately lower
than every other sport's 1000 (see this module's own constant) --
NCAA MBB's per-iteration cost is real regular-season games PLUS ~31
conference brackets PLUS First Four PLUS 4 regions PLUS Final Four/
Championship, all in the same iteration, unlike a single 12- or 8-team
bracket. Revisit this number once a real Lambda run's wall-clock time is
known (same "decide from real numbers, not a guess" discipline as this
project's own training_seconds tracking).
"""
import random
from typing import Callable

from library.features.common import DEFAULT_HOME_ADVANTAGE, DEFAULT_K_FACTOR, DEFAULT_STARTING_RATING, expected_score

DEFAULT_SIMULATIONS = 200
MARCH_MADNESS_FIELD_SIZE = 68
FIRST_FOUR_CONTESTED_PER_POOL = 4
REGION_COUNT = 4
REGION_NAMES = ["Region A", "Region B", "Region C", "Region D"]
MARCH_MADNESS_REGION_ROUND_NAMES = ["Round of 64", "Round of 32", "Sweet 16", "Elite Eight"]


def _standard_seed_line(bracket_size: int) -> list[int]:
    """1-indexed seeds in standard single-elimination bracket order for a
    field of exactly `bracket_size` (must be a power of 2) -- the same
    recursive expansion every real tournament seeding uses to guarantee
    seed 1 and seed 2 can't meet before the final: at every doubling, each
    existing seed s is paired with its complement (2 * len(seeds) + 1 -
    s), so adjacent seeds in the returned list are always a valid round-1
    matchup (their seeds always sum to bracket_size + 1) and the overall
    order keeps top seeds spread across separate halves/quarters/etc. all
    the way up.

    E.g. bracket_size=8 -> [1, 8, 4, 5, 2, 7, 3, 6], i.e. round-1 pairs
    (1v8), (4v5), (2v7), (3v6)."""
    seeds = [1]
    while len(seeds) < bracket_size:
        complement = len(seeds) * 2 + 1
        seeds = [s for pair in zip(seeds, [complement - s for s in seeds]) for s in pair]
    return seeds


def _next_power_of_two(n: int) -> int:
    size = 1
    while size < n:
        size *= 2
    return size


def project_matchup(
    team_a: str, team_b: str, seed_a: int | None, seed_b: int | None,
    ratings: dict[str, float], home_advantage: float,
) -> dict:
    """Deterministic single-matchup resolution -- no RNG, picks whichever
    side has >= 50% win probability. When both seeds are known, the
    better (numerically lower) seed always hosts; if either is None, or
    home_advantage is passed as 0.0 (every neutral-site round in this
    module), team_a/team_b keep their given order and home_advantage
    itself decides the framing. Used directly by First Four and the
    Final Four/Championship glue outside project_single_elim_bracket's
    own per-bracket walk -- same shape as NCAAFB's own project_matchup.

    Returns {"team_a", "team_b", "seed_a", "seed_b", "predicted_winner",
    "win_probability"} -- win_probability is always the WINNER's own
    probability, not "team_a's"."""
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


def _round_names(bracket_size: int) -> list[str]:
    """Round names counting down from bracket_size to the Championship,
    e.g. 16 -> ["Round of 16", "Quarterfinals", "Semifinals",
    "Championship"]; 8 -> ["Quarterfinals", "Semifinals", "Championship"];
    4 -> ["Semifinals", "Championship"]; 2 -> ["Championship"].

    Deliberately generic (no "Sweet 16"/"Elite Eight"/"Final Four") --
    this walker is shared by both conference tournaments (where "Round of
    16" is the real, only name; there's no Sweet-16-style branding at
    that scale) and March Madness's own 64-team tail. A March-Madness-
    specific caller can relabel this function's own "rounds" output
    afterward (same pattern NBA's own bracket rendering already applies
    for its "Conference Quarterfinals"/"Super Bowl"-style renames), not
    build the relabeling in here."""
    num_rounds = bracket_size.bit_length() - 1  # bracket_size is always a power of 2
    tail = [name for name, min_rounds in (("Quarterfinals", 3), ("Semifinals", 2), ("Championship", 1)) if num_rounds >= min_rounds]
    prefix = []
    size = bracket_size
    for _ in range(num_rounds - len(tail)):
        prefix.append(f"Round of {size}")
        size //= 2
    return prefix + tail


def project_single_elim_bracket(
    seeded_teams: list[str], ratings: dict[str, float], home_advantage: float = DEFAULT_HOME_ADVANTAGE,
) -> dict:
    """Deterministic single-elimination bracket for any field size --
    generalizes NCAAFB's project_cfp_bracket (hardcoded to 12) to however
    many rounds len(seeded_teams) (rounded up to the next power of two)
    requires. seeded_teams is seed order (index 0 = seed 1, the best);
    fields that aren't already a power of two are padded with byes at the
    bottom of the seed line (the standard seed-line property already
    pairs seed 1 against the lowest seed, so byes -- the "seeds" beyond
    the real field -- always land against the highest live seeds still
    in the bracket, auto-advancing them with no game).

    A bye round-1 matchup is recorded as {"team_a", "seed_a",
    "predicted_winner": team_a, "win_probability": 1.0, "team_b": None,
    "seed_b": None} rather than being silently skipped, so a caller
    rendering "rounds" doesn't need special-case logic to know a real
    game happened there or not.

    Only round 1 uses home_advantage (higher seed hosts) -- every later
    round is neutral-site (home_advantage=0.0), same as CFP's own
    Quarterfinals-on and every other cross-sport bracket in this project.

    Returns {"rounds": [{"round": "Round of <bracket_size>", "matchups":
    [...]}, ...], "champion": team_id}. Round names count down by half
    each time until "Championship" (e.g. 16 teams -> "Round of 16",
    "Quarterfinals", "Semifinals", "Championship" -- the same naming NBA/
    NCAAFB's own final rounds use, just with as many earlier rounds
    prepended as the field size needs)."""
    bracket_size = _next_power_of_two(len(seeded_teams))
    seed_line = _standard_seed_line(bracket_size)
    slot_team: dict[int, str | None] = {
        seed: (seeded_teams[seed - 1] if seed <= len(seeded_teams) else None) for seed in seed_line
    }

    def _matchup(team_a: str | None, seed_a: int, team_b: str | None, seed_b: int, advantage: float) -> dict:
        if team_b is None:
            return {
                "team_a": team_a, "seed_a": seed_a, "team_b": None, "seed_b": None,
                "predicted_winner": team_a, "win_probability": 1.0,
            }
        if team_a is None:
            return {
                "team_a": team_b, "seed_a": seed_b, "team_b": None, "seed_b": None,
                "predicted_winner": team_b, "win_probability": 1.0,
            }
        rating_a = ratings.get(team_a, DEFAULT_STARTING_RATING)
        rating_b = ratings.get(team_b, DEFAULT_STARTING_RATING)
        probability_a = expected_score(rating_a, rating_b, advantage)
        winner = team_a if probability_a >= 0.5 else team_b
        return {
            "team_a": team_a, "seed_a": seed_a, "team_b": team_b, "seed_b": seed_b,
            "predicted_winner": winner,
            "win_probability": probability_a if winner == team_a else 1 - probability_a,
        }

    round_names = _round_names(bracket_size)
    current_seeds = seed_line
    rounds = []
    advantage = home_advantage
    for round_name in round_names:
        matchups = []
        next_seeds = []
        for i in range(0, len(current_seeds), 2):
            seed_a, seed_b = current_seeds[i], current_seeds[i + 1]
            matchup = _matchup(slot_team[seed_a], seed_a, slot_team[seed_b], seed_b, advantage)
            matchups.append(matchup)
            winner = matchup["predicted_winner"]
            winner_seed = matchup["seed_a"] if winner == matchup["team_a"] else seed_b
            next_seeds.append(winner_seed)
            slot_team[winner_seed] = winner
        rounds.append({"round": round_name, "matchups": matchups})
        current_seeds = next_seeds
        advantage = 0.0  # every round after the first is neutral-site

    return {"rounds": rounds, "champion": rounds[-1]["matchups"][0]["predicted_winner"]}


def _play(team_a: str, team_b: str, ratings: dict[str, float], home_advantage: float, rng: random.Random) -> str:
    rating_a = ratings.get(team_a, DEFAULT_STARTING_RATING)
    rating_b = ratings.get(team_b, DEFAULT_STARTING_RATING)
    probability_a = expected_score(rating_a, rating_b, home_advantage)
    return team_a if rng.random() < probability_a else team_b


def _simulate_bracket_survivors(
    seeded_teams: list[str], ratings: dict[str, float], home_advantage: float, rng: random.Random,
) -> list[list[str]]:
    """Random (rng-driven) single-elimination simulation -- same seed-
    line/bye topology as project_single_elim_bracket, but stochastic
    instead of deterministic. Returns one list of surviving teams per
    round (a bye counts as an automatic survivor of that round); the
    final entry is a single-team list, that iteration's champion. Monte
    Carlo callers (simulate_season) use every round's survivor list, not
    just the champion, to track per-round reached-probability (e.g.
    "made the Sweet 16")."""
    bracket_size = _next_power_of_two(len(seeded_teams))
    seed_line = _standard_seed_line(bracket_size)
    slot_team: dict[int, str | None] = {
        seed: (seeded_teams[seed - 1] if seed <= len(seeded_teams) else None) for seed in seed_line
    }
    current_seeds = seed_line
    advantage = home_advantage
    survivors_per_round = []
    while len(current_seeds) > 1:
        next_seeds = []
        for i in range(0, len(current_seeds), 2):
            seed_a, seed_b = current_seeds[i], current_seeds[i + 1]
            team_a, team_b = slot_team[seed_a], slot_team[seed_b]
            if team_b is None:
                winner, winner_seed = team_a, seed_a
            elif team_a is None:
                winner, winner_seed = team_b, seed_b
            else:
                winner = _play(team_a, team_b, ratings, advantage, rng)
                winner_seed = seed_a if winner == team_a else seed_b
            slot_team[winner_seed] = winner
            next_seeds.append(winner_seed)
        current_seeds = next_seeds
        advantage = 0.0
        survivors_per_round.append([slot_team[seed] for seed in current_seeds])
    return survivors_per_round


def _group_by_conference(team_conference: dict[str, str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for team_id, conference in team_conference.items():
        if conference:
            result.setdefault(conference, []).append(team_id)
    return result


def _conference_seed_order(
    members: list[str], conference_wins: dict[str, int], conference_losses: dict[str, int],
    point_differential: dict[str, int],
) -> list[str]:
    """Seed order (best first) for one conference's own tournament --
    conference-only wins then point differential, no real head-to-head/
    RPI tiebreakers (same simplification NCAAFB's own _conference_champion
    already accepts). point_differential is each team's OVERALL season
    figure, not conference-only -- same "today's real value, not simulated
    or scoped further" simplification NCAAFB's own bracket seeding uses
    for its equivalent tiebreaker."""
    def sort_key(team_id: str) -> tuple[int, int]:
        return (-conference_wins.get(team_id, 0), -point_differential.get(team_id, 0))

    return sorted(members, key=sort_key)


def select_march_madness_field(
    model_scores: dict[str, float], conference_champions: dict[str, str],
) -> tuple[list[str], list[str]]:
    """(auto_bids, at_large), each in seed order (best model_score
    first). auto_bids is one team per conference (conference_champions'
    own distinct values) -- len(conferences) automatic bids, NOT a
    hardcoded 32 or 68, since real D1 realignment changes the conference
    count from one season to the next. at_large fills the rest of the
    MARCH_MADNESS_FIELD_SIZE-team field with the best-ranked remaining
    teams. Lower model_scores is better, same convention as every other
    ranking-model consumer in this project; a team missing from
    model_scores sorts last."""
    auto_bids = sorted(set(conference_champions.values()), key=lambda t: model_scores.get(t, float("inf")))
    pool = sorted((t for t in model_scores if t not in auto_bids), key=lambda t: model_scores[t])
    at_large = pool[: max(0, MARCH_MADNESS_FIELD_SIZE - len(auto_bids))]
    return auto_bids, at_large


def _first_four_pools(auto_bids: list[str], at_large: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Splits (auto_bids, at_large) into (settled, contested_auto,
    contested_at_large) -- the weakest FIRST_FOUR_CONTESTED_PER_POOL
    teams of EACH pool play their way in (2 games/pool, 4 games total);
    everyone else already holds a real Round-of-64 slot. settled is both
    pools' untouched teams, still in their own relative seed order."""
    contested_auto = auto_bids[-min(FIRST_FOUR_CONTESTED_PER_POOL, len(auto_bids)):]
    contested_at_large = at_large[-min(FIRST_FOUR_CONTESTED_PER_POOL, len(at_large)):]
    settled = auto_bids[: len(auto_bids) - len(contested_auto)] + at_large[: len(at_large) - len(contested_at_large)]
    return settled, contested_auto, contested_at_large


def project_first_four(auto_bids: list[str], at_large: list[str], ratings: dict[str, float], model_scores: dict[str, float]) -> dict:
    """First Four: the 4 weakest automatic bids and 4 weakest at-large
    teams (2 games/pool) play in on a neutral site to trim the field to
    64 -- structurally identical to NBA's own Play-In (project_play_in):
    glue around the fixed bracket that follows, not its own generic
    walker. A winner takes the single Round-of-64 slot its own pair
    shared, matching the frontend's existing skip-connector UI for a
    Play-In-style "winner skips ahead" case (see this module's own
    docstring) with zero new frontend code needed.

    Returns {"matchups": [...], "field": <64-team list, re-sorted by
    model_scores so callers can region-seed it directly>}."""
    settled, contested_auto, contested_at_large = _first_four_pools(auto_bids, at_large)

    matchups = []
    field = list(settled)
    for pool in (contested_auto, contested_at_large):
        for i in range(0, len(pool) - 1, 2):
            matchup = project_matchup(pool[i], pool[i + 1], None, None, ratings, 0.0)
            matchups.append(matchup)
            field.append(matchup["predicted_winner"])

    field.sort(key=lambda t: model_scores.get(t, float("inf")))
    return {"matchups": matchups, "field": field}


def _assign_regions(seeded_field: list[str]) -> dict[str, list[str]]:
    """Standard S-curve/snake seeding of a 64-team seed-ordered field
    (best first) into REGION_COUNT regions of 16: seed line 1 (seeds
    1-4) goes to regions A, B, C, D in order; seed line 2 (seeds 5-8)
    goes D, C, B, A (reversed); and so on, alternating direction every
    4 teams -- the same snake-draft pattern real tournament committees
    use so region strength stays balanced (no region gets both the
    overall #1 and #2 seed)."""
    regions: dict[str, list[str]] = {name: [] for name in REGION_NAMES}
    for line_start in range(0, len(seeded_field), REGION_COUNT):
        line = seeded_field[line_start: line_start + REGION_COUNT]
        names = REGION_NAMES if (line_start // REGION_COUNT) % 2 == 0 else list(reversed(REGION_NAMES))
        for name, team_id in zip(names, line):
            regions[name].append(team_id)
    return regions


def project_march_madness_bracket(
    auto_bids: list[str], at_large: list[str], ratings: dict[str, float], model_scores: dict[str, float],
) -> dict:
    """Deterministic full March Madness bracket: First Four trims to 64,
    the 64 are snake-seeded into 4 regions of 16 (project_single_elim_
    bracket per region, always neutral-site -- every NCAA tournament game
    is played at a neutral site, unlike a conference tournament's
    campus-site early rounds), region champions meet at a Final Four,
    then a Championship.

    Flattened into ONE combined round list (not 4 separate per-region
    brackets) so the frontend's existing flat-`rounds` BracketProjection
    model needs no new shape -- same "conferences converge into the
    Championship card" precedent NBA/NFL's own bracket rendering already
    established. Each region's own generic round names (see
    project_single_elim_bracket's own _round_names) are relabeled here
    with real March Madness terminology (Round of 64/32, Sweet 16, Elite
    Eight) since this caller, unlike the shared walker itself, knows
    it's specifically March Madness.

    Returns {"rounds": [...], "champion": team_id}."""
    first_four = project_first_four(auto_bids, at_large, ratings, model_scores)
    regions = _assign_regions(first_four["field"])
    region_brackets = {name: project_single_elim_bracket(teams, ratings, home_advantage=0.0) for name, teams in regions.items()}

    rounds = [{"round": "First Four", "matchups": first_four["matchups"]}]
    for round_index, round_name in enumerate(MARCH_MADNESS_REGION_ROUND_NAMES):
        matchups = [
            matchup
            for region_name in REGION_NAMES
            for matchup in region_brackets[region_name]["rounds"][round_index]["matchups"]
        ]
        rounds.append({"round": round_name, "matchups": matchups})

    region_champions = [region_brackets[name]["champion"] for name in REGION_NAMES]
    final_four_matchups = [
        project_matchup(region_champions[0], region_champions[1], None, None, ratings, 0.0),
        project_matchup(region_champions[2], region_champions[3], None, None, ratings, 0.0),
    ]
    rounds.append({"round": "Final Four", "matchups": final_four_matchups})

    championship_matchup = project_matchup(
        final_four_matchups[0]["predicted_winner"], final_four_matchups[1]["predicted_winner"], None, None, ratings, 0.0,
    )
    rounds.append({"round": "Championship", "matchups": [championship_matchup]})

    return {"rounds": rounds, "champion": championship_matchup["predicted_winner"]}


def simulate_season(
    current_wins: dict[str, int],
    current_losses: dict[str, int],
    current_conference_wins: dict[str, int],
    current_conference_losses: dict[str, int],
    point_differential: dict[str, int],
    remaining_games: list[tuple[str, str, bool]],
    current_ratings: dict[str, float],
    team_conference: dict[str, str],
    score_teams: Callable[[dict[str, int], dict[str, int], dict[str, float]], dict[str, float]],
    simulations: int = DEFAULT_SIMULATIONS,
    home_advantage: float = DEFAULT_HOME_ADVANTAGE,
    k_factor: float = DEFAULT_K_FACTOR,
    rng: random.Random | None = None,
) -> dict[str, dict]:
    """Runs `simulations` Monte Carlo season paths from today's actual
    record forward: remaining REGULAR-SEASON games only (each a (home_id,
    away_id, is_conference_game) triple; conference-tournament/NCAA-
    tournament games are never in this list -- they're simulated
    separately, below, once the regular season resolves each iteration).

    team_conference is {team_id: conference}; the tracked team set is
    derived from its keys. win/loss totals in the returned dict stop at
    the end of the regular season, same convention as NCAAFB's own
    simulate_season -- tournament outcomes are tracked as separate
    probability fields, not folded into win/loss totals.

    score_teams is called once per simulated season with that
    iteration's final (wins, losses, ratings) and must return a
    {team_id: score} dict (lower is better) for every tracked team --
    used only to seed March Madness's at-large field, not game outcomes.
    """
    rng = rng or random.Random()
    teams = set(team_conference)
    conferences = _group_by_conference(team_conference)

    win_totals = {team_id: 0.0 for team_id in teams}
    loss_totals = {team_id: 0.0 for team_id in teams}
    conference_tournament_champion_totals = {team_id: 0 for team_id in teams}
    tournament_totals = {team_id: 0 for team_id in teams}
    first_four_totals = {team_id: 0 for team_id in teams}
    round_of_64_totals = {team_id: 0 for team_id in teams}
    sweet_16_totals = {team_id: 0 for team_id in teams}
    elite_eight_totals = {team_id: 0 for team_id in teams}
    final_four_totals = {team_id: 0 for team_id in teams}
    championship_game_totals = {team_id: 0 for team_id in teams}
    champion_totals = {team_id: 0 for team_id in teams}

    for _ in range(simulations):
        wins = {team_id: current_wins.get(team_id, 0) for team_id in teams}
        losses = {team_id: current_losses.get(team_id, 0) for team_id in teams}
        conference_wins = {team_id: current_conference_wins.get(team_id, 0) for team_id in teams}
        conference_losses = {team_id: current_conference_losses.get(team_id, 0) for team_id in teams}
        ratings = dict(current_ratings)

        for home_id, away_id, is_conference in remaining_games:
            if home_id not in teams or away_id not in teams:
                continue
            home_rating = ratings.get(home_id, DEFAULT_STARTING_RATING)
            away_rating = ratings.get(away_id, DEFAULT_STARTING_RATING)
            home_win_probability = expected_score(home_rating, away_rating, home_advantage)
            home_won = rng.random() < home_win_probability

            wins[home_id if home_won else away_id] += 1
            losses[away_id if home_won else home_id] += 1
            if is_conference:
                conference_wins[home_id if home_won else away_id] += 1
                conference_losses[away_id if home_won else home_id] += 1

            actual_home = 1.0 if home_won else 0.0
            ratings[home_id] = home_rating + k_factor * (actual_home - home_win_probability)
            ratings[away_id] = away_rating + k_factor * ((1 - actual_home) - (1 - home_win_probability))

        for team_id in teams:
            win_totals[team_id] += wins[team_id]
            loss_totals[team_id] += losses[team_id]

        conference_champions: dict[str, str] = {}
        for conference, members in conferences.items():
            seed_order = _conference_seed_order(members, conference_wins, conference_losses, point_differential)
            survivors = _simulate_bracket_survivors(seed_order, ratings, home_advantage, rng)
            champion = survivors[-1][0]
            conference_champions[conference] = champion
            conference_tournament_champion_totals[champion] += 1

        model_scores = score_teams(wins, losses, ratings)
        auto_bids, at_large = select_march_madness_field(model_scores, conference_champions)
        for team_id in auto_bids + at_large:
            tournament_totals[team_id] += 1

        settled, contested_auto, contested_at_large = _first_four_pools(auto_bids, at_large)
        field = list(settled)
        for pool in (contested_auto, contested_at_large):
            for i in range(0, len(pool) - 1, 2):
                for team_id in (pool[i], pool[i + 1]):
                    first_four_totals[team_id] += 1
                winner = _play(pool[i], pool[i + 1], ratings, 0.0, rng)
                field.append(winner)
                round_of_64_totals[winner] += 1
        for team_id in settled:
            round_of_64_totals[team_id] += 1

        field.sort(key=lambda t: model_scores.get(t, float("inf")))
        regions = _assign_regions(field)
        region_champions = []
        for region_teams in regions.values():
            # Indexed from the end, not from the start: a real 16-team
            # region always has exactly 4 survivor rounds (so -3/-2/-1
            # line up with Sweet 16/Elite Eight/Final Four exactly), but a
            # smaller region (a sparse-data test fixture, or an early-
            # season run with fewer than 68 teams tracked) has fewer
            # rounds -- negative indexing degrades gracefully instead of
            # raising IndexError, just skipping the credit for a stage
            # that region's bracket size doesn't actually have.
            survivors = _simulate_bracket_survivors(region_teams, ratings, 0.0, rng)
            if len(survivors) >= 3:
                for team_id in survivors[-3]:
                    sweet_16_totals[team_id] += 1
            if len(survivors) >= 2:
                for team_id in survivors[-2]:
                    elite_eight_totals[team_id] += 1
            for team_id in survivors[-1]:
                final_four_totals[team_id] += 1
            region_champions.append(survivors[-1][0])

        semifinal_winners = [
            _play(region_champions[0], region_champions[1], ratings, 0.0, rng),
            _play(region_champions[2], region_champions[3], ratings, 0.0, rng),
        ]
        for team_id in semifinal_winners:
            championship_game_totals[team_id] += 1
        champion = _play(semifinal_winners[0], semifinal_winners[1], ratings, 0.0, rng)
        champion_totals[champion] += 1

    return {
        team_id: {
            "projected_wins": win_totals[team_id] / simulations,
            "projected_losses": loss_totals[team_id] / simulations,
            "conference_tournament_champion_probability": conference_tournament_champion_totals[team_id] / simulations,
            "ncaa_tournament_probability": tournament_totals[team_id] / simulations,
            "first_four_probability": first_four_totals[team_id] / simulations,
            "round_of_64_probability": round_of_64_totals[team_id] / simulations,
            "sweet_16_probability": sweet_16_totals[team_id] / simulations,
            "elite_eight_probability": elite_eight_totals[team_id] / simulations,
            "final_four_probability": final_four_totals[team_id] / simulations,
            "championship_game_probability": championship_game_totals[team_id] / simulations,
            "national_champion_probability": champion_totals[team_id] / simulations,
        }
        for team_id in teams
    }
