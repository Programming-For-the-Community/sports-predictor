"""
Monte Carlo playoff brackets, started from the real postseason so far.

A sport describes its bracket as a `topology(play_round)` function: it
calls `play_round(round_name, pairs, home_advantage)` once per round, in a
fixed order, with [(team_a, team_b, seed_a, seed_b), ...], gets back
(round_payload, [(winner, winner_seed), ...]), and returns a dict holding at
least "champion". The same topology runs once per simulation and once more
to build the display.

In every simulation a real decided game or series keeps its real winner; a
series under way is simulated from its real record; everything else is
simulated from the sport's own per-game odds (`MatchupRules`). The bracket
shown is one of those runs: of the runs with the typical number of upsets,
the most likely one. Round odds count every run.

Who's favoured in a slot, else the better seed, else the sport's `tiebreak`
(better record):
  - a single game: the sportsbook favourite on its pre-game line.
  - a series: the team the betting market makes likelier to win the series,
    from Game 1's line (series_win_chance) once Game 1 has locked or
    started. It doesn't change after that.
UPSET = the favourite lost; UPSET WATCH = the favourite won but the model
gave it no more than UPSET_WATCH_MAX_FAVORITE_PERCENT before the game or
series began.
"""
import hashlib
import math
import random
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from library.features.common import DEFAULT_STARTING_RATING, expected_score
from library.normalize.odds import LOCKED_AT, PREGAME_ODDS, no_vig_home_chance
from library.serving.common import _actual_result, _home_and_away, betting_favorite

SIMULATIONS = 2000
UPSET_WATCH_MAX_FAVORITE_PERCENT = 60

Pair = tuple[str | None, str | None, int | None, int | None]

# Game 1's host is at home in games 1, 2, 5 and 7 of a best-of-seven (2-2-1-1-1).
SERIES_HOME_PATTERN = (True, True, False, False, True, False, True)
# Fewer decided games than this and the home edge isn't measured (0).
MIN_GAMES_FOR_HOME_EDGE = 200


@dataclass(frozen=True)
class MatchupRules:
    """One slot's format: wins needed (1 = single game) and team_a's chance
    in each game, by game number from 0."""

    wins_needed: int
    game_probability: Callable[[int], float]


def series_probability(game_probability: Callable[[int], float], wins_needed: int, wins_a: int = 0, wins_b: int = 0) -> float:
    """team_a's chance to reach `wins_needed` first from this record."""
    memo: dict[tuple[int, int], float] = {}

    def chance(a: int, b: int) -> float:
        if a >= wins_needed:
            return 1.0
        if b >= wins_needed:
            return 0.0
        if (a, b) not in memo:
            p = game_probability(a + b)
            memo[(a, b)] = p * chance(a + 1, b) + (1 - p) * chance(a, b + 1)
        return memo[(a, b)]

    return chance(wins_a, wins_b)


def _logit(p: float) -> float:
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def measured_home_edge(events: list[dict]) -> float:
    """The home side's edge in these completed games, in log-odds: the
    logit of the home win share. 0 with fewer than MIN_GAMES_FOR_HOME_EDGE
    decided games."""
    results = [_actual_result(event) for event in events if event.get("status") == "completed"]
    home_won = [r["home_won"] for r in results if r is not None and r["home_score"] != r["away_score"]]
    if len(home_won) < MIN_GAMES_FOR_HOME_EDGE:
        return 0.0
    share = sum(home_won) / len(home_won)
    return _logit(share) if 0 < share < 1 else 0.0


def series_win_chance(host_game_one_chance: float, home_edge: float, wins_needed: int) -> float:
    """Game 1's host's chance to win the series: its Game 1 chance with the
    home edge taken out is its neutral strength, and each game adds the edge
    back for whoever hosts it (SERIES_HOME_PATTERN)."""
    strength = _logit(host_game_one_chance) - home_edge
    home, away = _sigmoid(strength + home_edge), _sigmoid(strength - home_edge)
    return series_probability(
        lambda i: home if SERIES_HOME_PATTERN[i % len(SERIES_HOME_PATTERN)] else away, wins_needed,
    )


def elo_rules(ratings: dict[str, float], wins_needed: Callable[[str], int] = lambda round_name: 1):
    """Elo odds with team_a -- the better seed, as every topology lists it --
    hosting, the same in every game of a series."""
    def rules(round_name: str, team_a: str, team_b: str, home_advantage: float) -> MatchupRules:
        p = expected_score(
            ratings.get(team_a, DEFAULT_STARTING_RATING), ratings.get(team_b, DEFAULT_STARTING_RATING), home_advantage,
        )
        return MatchupRules(wins_needed=wins_needed(round_name), game_probability=lambda i: p)
    return rules


class RealPostseason:
    """The real postseason games so far, by pair. `logged_home_probability`
    gives a real game's own pre-game prediction (the home side's chance),
    or None."""

    def __init__(self, events: list[dict], logged_home_probability: Callable[[dict], float | None]):
        self._games: dict[frozenset, list[dict]] = {}
        for event in events:
            home_away = _home_and_away(event)
            if home_away is not None:
                self._games.setdefault(frozenset(home_away), []).append(event)
        for games in self._games.values():
            games.sort(key=lambda e: (e.get("kickoff_time") or e.get("event_date") or ""))
        self._logged = logged_home_probability
        self._probability_cache: dict[str, float | None] = {}

    def games(self, team_a: str, team_b: str) -> list[dict]:
        return self._games.get(frozenset((team_a, team_b)), [])

    def record(self, team_a: str, team_b: str) -> tuple[int, int]:
        wins_a = wins_b = 0
        for game in self.games(team_a, team_b):
            if game.get("status") != "completed":
                continue
            home_id, away_id = _home_and_away(game)
            winner = home_id if _actual_result(game)["home_won"] else away_id
            wins_a += winner == team_a
            wins_b += winner == team_b
        return wins_a, wins_b

    def chance_for(self, game: dict, team_a: str) -> float | None:
        """team_a's own logged pre-game chance in `game`."""
        key = game["event_key"]
        if key not in self._probability_cache:
            self._probability_cache[key] = self._logged(game)
        home_probability = self._probability_cache[key]
        if home_probability is None:
            return None
        return home_probability if _home_and_away(game)[0] == team_a else 1 - home_probability

    def next_game(self, team_a: str, team_b: str) -> dict | None:
        return next((g for g in self.games(team_a, team_b) if g.get("status") != "completed"), None)

    def favorite(self, team_a: str, team_b: str) -> str | None:
        """The sportsbook favourite on the first real game's line."""
        games = self.games(team_a, team_b)
        side = betting_favorite(games[0]) if games else None
        if side is None:
            return None
        home_id, away_id = _home_and_away(games[0])
        return home_id if side == "home" else away_id

    def series_favorite(self, team_a: str, team_b: str, wins_needed: int, home_edge: float) -> str | None:
        """The market's series favourite from Game 1's line (series_win_chance).
        None until Game 1's line is locked or Game 1 has started, without
        both moneylines, or for a dead-even series."""
        games = self.games(team_a, team_b)
        if not games:
            return None
        first = games[0]
        line = first.get(PREGAME_ODDS) or {}
        if LOCKED_AT not in line and first.get("status") == "scheduled":
            return None
        host_chance = no_vig_home_chance(line)
        if host_chance is None or not 0 < host_chance < 1:
            return None
        host_series = series_win_chance(host_chance, home_edge, wins_needed)
        if math.isclose(host_series, 0.5):
            return None
        host, visitor = _home_and_away(first)
        return host if host_series > 0.5 else visitor

    def fingerprint(self) -> str:
        """Changes only when a real game is added or decided."""
        parts = sorted(
            f"{g['event_key']}:{g.get('status')}:{_actual_result(g)['home_won'] if g.get('status') == 'completed' else ''}"
            for games in self._games.values() for g in games
        )
        return hashlib.sha256("|".join(parts).encode()).hexdigest()


@dataclass
class SlotResult:
    round: str
    team_a: str | None
    team_b: str | None
    seed_a: int | None
    seed_b: int | None
    winner: str | None
    wins_needed: int = 1
    wins_a: int = 0
    wins_b: int = 0
    real_wins_a: int = 0
    real_wins_b: int = 0
    real_games: bool = False
    decided: bool = False
    chance_now_a: float | None = None
    chance_pre_a: float | None = None
    favorite: str | None = None
    log_likelihood: float = 0.0
    final_game: dict | None = None

    @property
    def is_bye(self) -> bool:
        return self.team_a is None or self.team_b is None

    @property
    def is_upset(self) -> bool:
        return not self.is_bye and self.favorite is not None and self.winner != self.favorite

    def flag(self) -> str | None:
        if self.is_bye or self.favorite is None:
            return None
        if self.winner != self.favorite:
            return "upset"
        chance = self.chance_pre_a if self.favorite == self.team_a else 1 - self.chance_pre_a
        # Half up, the way the app rounds the percent it shows.
        return "watch" if math.floor(chance * 100 + 0.5) <= UPSET_WATCH_MAX_FAVORITE_PERCENT else None


@dataclass
class _Run:
    slots: dict[tuple[str, int], SlotResult] = field(default_factory=dict)
    champion: str | None = None

    @property
    def upsets(self) -> int:
        return sum(slot.is_upset for slot in self.slots.values())

    @property
    def log_likelihood(self) -> float:
        return sum(slot.log_likelihood for slot in self.slots.values())


class BracketSimulation:
    """`rules(round_name, team_a, team_b, home_advantage)` is the sport's
    MatchupRules for a slot; `tiebreak(team_a, team_b)` names the better
    team when neither the line nor the seeds settle who's favoured;
    `home_edge` is the sport's measured_home_edge, for series favourites."""

    def __init__(
        self, real: RealPostseason, rules: Callable[[str, str, str, float], MatchupRules], *,
        tiebreak: Callable[[str, str], str | None] = lambda a, b: None, simulations: int = SIMULATIONS,
        home_edge: float = 0.0,
    ):
        self._real = real
        self._rules = rules
        self._tiebreak = tiebreak
        self._simulations = simulations
        self._home_edge = home_edge

    def _favorite(self, team_a: str, team_b: str, seed_a: int | None, seed_b: int | None, wins_needed: int) -> str | None:
        if wins_needed > 1:
            line = self._real.series_favorite(team_a, team_b, wins_needed, self._home_edge)
        else:
            line = self._real.favorite(team_a, team_b)
        if line is not None:
            return line
        if seed_a is not None and seed_b is not None and seed_a != seed_b:
            return team_a if seed_a < seed_b else team_b
        return self._tiebreak(team_a, team_b)

    def _odds(self, round_name: str, team_a: str, team_b: str, home_advantage: float) -> tuple[MatchupRules, Callable[[int], float]]:
        """The sport's rules, with the real next game's own logged chance
        in place of the model's for that one game."""
        rules = self._rules(round_name, team_a, team_b, home_advantage)
        next_game = self._real.next_game(team_a, team_b)
        logged = self._real.chance_for(next_game, team_a) if next_game is not None else None
        if logged is None:
            return rules, rules.game_probability
        wins_a, wins_b = self._real.record(team_a, team_b)
        next_index = wins_a + wins_b
        return rules, lambda i: logged if i == next_index else rules.game_probability(i)

    def _pre_start_chance(self, rules: MatchupRules, team_a: str, team_b: str) -> float:
        games = self._real.games(team_a, team_b)
        first = self._real.chance_for(games[0], team_a) if games else None
        if first is None:
            return series_probability(rules.game_probability, rules.wins_needed)
        return series_probability(lambda i: first if i == 0 else rules.game_probability(i), rules.wins_needed)

    def _slot(self, round_name: str, pair: Pair, home_advantage: float, rng: random.Random | None) -> SlotResult:
        team_a, team_b, seed_a, seed_b = pair
        if team_a is None or team_b is None:
            return SlotResult(round_name, team_a, team_b, seed_a, seed_b, winner=team_a or team_b)
        rules, probability = self._odds(round_name, team_a, team_b, home_advantage)
        real_wins_a, real_wins_b = self._real.record(team_a, team_b)
        games = self._real.games(team_a, team_b)
        decided = max(real_wins_a, real_wins_b) >= rules.wins_needed
        slot = SlotResult(
            round_name, team_a, team_b, seed_a, seed_b, winner=None, wins_needed=rules.wins_needed,
            real_wins_a=real_wins_a, real_wins_b=real_wins_b, real_games=bool(games), decided=decided,
            chance_pre_a=self._pre_start_chance(rules, team_a, team_b),
            favorite=self._favorite(team_a, team_b, seed_a, seed_b, rules.wins_needed),
        )
        if decided:
            slot.winner = team_a if real_wins_a >= rules.wins_needed else team_b
            slot.wins_a, slot.wins_b = real_wins_a, real_wins_b
            slot.chance_now_a = slot.chance_pre_a
            if rules.wins_needed == 1:
                slot.final_game = next(g for g in games if g.get("status") == "completed")
            return slot
        slot.chance_now_a = series_probability(probability, rules.wins_needed, real_wins_a, real_wins_b)
        wins_a, wins_b = real_wins_a, real_wins_b
        while max(wins_a, wins_b) < rules.wins_needed:
            p = probability(wins_a + wins_b)
            # Display mode (no rng) plays every remaining game to its favourite.
            a_wins = rng.random() < p if rng is not None else p >= 0.5
            wins_a, wins_b = wins_a + a_wins, wins_b + (not a_wins)
        slot.wins_a, slot.wins_b = wins_a, wins_b
        slot.winner = team_a if wins_a >= rules.wins_needed else team_b
        chance = slot.chance_now_a if slot.winner == team_a else 1 - slot.chance_now_a
        slot.log_likelihood = math.log(max(chance, 1e-12))
        return slot

    def _play(self, topology, play_slot) -> dict:
        counters: dict[str, int] = {}

        def play_round(round_name: str, pairs: list[Pair], home_advantage: float = 0.0):
            payload_rows, advancing = [], []
            for pair in pairs:
                index = counters.get(round_name, 0)
                counters[round_name] = index + 1
                slot, row = play_slot((round_name, index), round_name, pair, home_advantage)
                payload_rows.append(row)
                advancing.append((slot.winner, slot.seed_a if slot.winner == slot.team_a else slot.seed_b))
            return {"round": round_name, "matchups": payload_rows}, advancing

        return topology(play_round)

    def run(self, topology, columns: list[tuple[str, str | None]], group_of: Callable[[str], str | None]) -> dict:
        """The display bracket (the topology's own dict, its rounds' rows
        built from the chosen run) plus "round_odds". `columns` is
        [(label, round_name), ...]; a None round_name is the title."""
        rng = random.Random(int(self._real.fingerprint()[:16], 16))
        runs = []
        for _ in range(self._simulations):
            run = _Run()

            def simulate(key, round_name, pair, home_advantage, run=run):
                slot = self._slot(round_name, pair, home_advantage, rng)
                run.slots[key] = slot
                return slot, None

            run.champion = self._play(topology, simulate).get("champion")
            runs.append(run)

        chosen = _representative(runs)

        def show(key, round_name, pair, home_advantage):
            slot = chosen.slots.get(key)
            if slot is None or (slot.team_a, slot.team_b) != pair[:2]:
                slot = self._slot(round_name, pair, home_advantage, None)
            return slot, _row(slot)

        bracket = self._play(topology, show)
        bracket["round_odds"] = _round_odds(runs, chosen, columns, group_of)
        bracket["simulations"] = len(runs)
        return bracket


def _representative(runs: list[_Run]) -> _Run:
    """Of the runs with the typical number of upsets, the most likely."""
    target = round(sum(run.upsets for run in runs) / len(runs))
    closest = min(abs(run.upsets - target) for run in runs)
    candidates = [run for run in runs if abs(run.upsets - target) == closest]
    return max(candidates, key=lambda run: run.log_likelihood)


def _status(slot: SlotResult) -> str:
    if slot.decided:
        return "final"
    return "scheduled" if slot.real_games else "projected"


def _pick_fields(slot: SlotResult) -> dict:
    """The model's pick: once decided, what it expected before the start;
    before then, the shown run's winner."""
    if slot.decided:
        chance_a = slot.chance_pre_a
        pick = slot.team_a if chance_a >= 0.5 else slot.team_b
        extra = {"actual_winner": slot.winner}
    else:
        chance_a = slot.chance_now_a
        pick = slot.winner
        extra = {}
    return {**extra, "predicted_winner": pick, "win_probability": chance_a if pick == slot.team_a else 1 - chance_a}


def _result_fields(slot: SlotResult) -> dict:
    """A series' records (real so far, and as the shown run ends it), or a
    finished single game's score."""
    if slot.wins_needed > 1:
        fields = {
            "wins_a": slot.real_wins_a, "wins_b": slot.real_wins_b,
            "series_wins_a": slot.wins_a, "series_wins_b": slot.wins_b,
        }
        if not slot.decided:
            fields |= {"predicted_wins_a": slot.wins_a, "predicted_wins_b": slot.wins_b}
        return fields
    if slot.final_game is None:
        return {}
    actual = _actual_result(slot.final_game)
    return {"actual_home_score": actual["home_score"], "actual_away_score": actual["away_score"]}


def _row(slot: SlotResult) -> dict:
    """One bracket card. Keeps every field older app builds read: status,
    predicted_winner/win_probability, actual_winner, wins_a/wins_b (the real
    series record so far), predicted_wins_a/b."""
    row = {
        "team_a": slot.team_a, "team_b": slot.team_b, "seed_a": slot.seed_a, "seed_b": slot.seed_b,
        "status": _status(slot),
    }
    if slot.is_bye:
        return {**row, "predicted_winner": slot.winner, "win_probability": 1.0}
    return {
        **row,
        "chance_a": slot.chance_now_a, "chance_b": 1 - slot.chance_now_a,
        "favorite": slot.favorite, "flag": slot.flag(),
        **_pick_fields(slot), **_result_fields(slot),
    }


def _sides(slot: SlotResult):
    return [(team, seed) for team, seed in ((slot.team_a, slot.seed_a), (slot.team_b, slot.seed_b)) if team is not None]


def _seeds(runs: list[_Run], chosen: _Run) -> dict[str, int | None]:
    """Each team's seed, the displayed bracket's own winning over any other run's."""
    seeds: dict[str, int | None] = {}
    for run in runs:
        for slot in run.slots.values():
            for team, seed in _sides(slot):
                seeds.setdefault(team, seed)
    for slot in chosen.slots.values():
        seeds.update((team, seed) for team, seed in _sides(slot) if seed is not None)
    return seeds


def _round_odds(runs: list[_Run], chosen: _Run, columns: list[tuple[str, str | None]], group_of) -> dict:
    reach = Counter((team, round_name) for run in runs for (round_name, _), slot in run.slots.items() for team, _ in _sides(slot))
    titles = Counter(run.champion for run in runs if run.champion is not None)
    seeds = _seeds(runs, chosen)
    total = len(runs)

    groups: dict[str, list[dict]] = {}
    for team in sorted({key[0] for key in reach}):
        values = [(titles[team] if round_name is None else reach[(team, round_name)]) / total for _, round_name in columns]
        groups.setdefault(group_of(team) or "", []).append({"team_id": team, "seed": seeds.get(team), "values": values})
    for rows in groups.values():
        rows.sort(key=lambda r: (r["seed"] is None, r["seed"] or 0, -r["values"][-1]))
    return {"columns": [label for label, _ in columns], "groups": groups}
