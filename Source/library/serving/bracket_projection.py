"""
Playoff-bracket slot resolution shared by every head-to-head sport's own
predict/season_projection.py (NFL/NBA/NCAAFB/NCAA MBB). Each slot is one
of three states: "projected" (no real postseason game for the pair yet --
the model's own deterministic pick), "scheduled" (a real game exists, not
yet played -- its live prediction), or "final" (a real game exists and is
completed -- the actual result plus whatever was predicted for it).

The per-sport pieces -- computing a live prediction and projecting an
unplayed matchup -- come from the sport's own event_prediction and
season_simulation modules, looked up at call time.
"""
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from types import ModuleType

from boto3.dynamodb.conditions import Key

from library.serving.common import WIN_PROBABILITY_MODEL, _actual_result, _home_and_away, latest_matching_row

# (team_a, team_b, seed_a, seed_b) -- team_b is None for a bye.
BracketPair = tuple[str, str | None, int | None, int | None]


def real_postseason_matchups(
    storage, sport: str, current_season: int | None, is_postseason: Callable[[dict], bool],
) -> dict[frozenset, dict]:
    """{frozenset({home_id, away_id}): event} for every real postseason
    game this season, scheduled or completed. get_all_events has no
    "every status at once" option, so both statuses are fetched and
    merged."""
    result: dict[frozenset, dict] = {}
    for status in ("scheduled", "completed"):
        for event in storage.get_all_events(sport, status=status):
            if event.get("season") != current_season or not is_postseason(event):
                continue
            home_away = _home_and_away(event)
            if home_away is not None:
                result[frozenset(home_away)] = event
    return result


def logged_win_probability(predictions_table, event_key_value: str) -> dict | None:
    """This event's own logged win-probability prediction, if anyone ever
    requested one -- never recomputed, since recomputing after the fact
    could build live features that already include this game's own
    result."""
    rows = predictions_table.query(Key("event_key").eq(event_key_value))
    row = latest_matching_row(rows, WIN_PROBABILITY_MODEL)
    return row["predicted_value"] if row else None


def predicted_winner_and_probability(
    logged: dict | None, home_id: str, away_id: str,
) -> tuple[str | None, float | None]:
    """(predicted_winner, win_probability) from a logged win-probability
    prediction, or (None, None) if none was ever logged."""
    if logged is None:
        return None, None
    probability = logged["home_win_probability"]
    predicted_winner = home_id if probability >= 0.5 else away_id
    win_probability = probability if predicted_winner == home_id else 1 - probability
    return predicted_winner, win_probability


def completed_matchup_row(
    real_event: dict, event_key_value: str, home_id: str, away_id: str, seed_a: int | None, seed_b: int | None,
    predictions_table,
) -> dict:
    actual = _actual_result(real_event)
    logged = logged_win_probability(predictions_table, event_key_value)
    predicted_winner, win_probability = predicted_winner_and_probability(logged, home_id, away_id)
    return {
        "status": "final",
        "team_a": home_id, "team_b": away_id, "seed_a": seed_a, "seed_b": seed_b,
        "predicted_winner": predicted_winner, "win_probability": win_probability,
        "actual_winner": home_id if actual["home_won"] else away_id,
        "actual_home_score": actual["home_score"], "actual_away_score": actual["away_score"],
    }


def _advancing_team(matchup: dict, team_a: str, seed_a: int | None, seed_b: int | None) -> tuple[str, int | None]:
    """The slot's winner (real once final, else predicted) and its own
    seed, carried forward unchanged -- a team isn't reseeded mid-run."""
    winner = matchup["actual_winner"] if matchup["status"] == "final" else matchup["predicted_winner"]
    return winner, seed_a if winner == team_a else seed_b


@dataclass(frozen=True)
class BracketResolver:
    """`event_prediction.compute_and_cache_event` computes and logs a live
    prediction for a real, unplayed game; `season_simulation.
    project_matchup` is the model's own deterministic pick for a pair with
    no real game yet."""

    event_prediction: ModuleType
    season_simulation: ModuleType
    logger: logging.Logger

    def scheduled_matchup_row(
        self, real_event: dict, event_key_value: str, home_id: str, away_id: str,
        seed_a: int | None, seed_b: int | None, storage, s3, predictions_table,
    ) -> dict:
        """Computes the game's live prediction in-process when nobody has
        viewed it yet."""
        logged = logged_win_probability(predictions_table, event_key_value)
        if logged is None:
            try:
                self.event_prediction.compute_and_cache_event(storage, s3, predictions_table, real_event["event_id"])
                logged = logged_win_probability(predictions_table, event_key_value)
            except Exception:
                self.logger.exception("Failed computing a live prediction for bracket game %s", event_key_value)

        predicted_winner, win_probability = predicted_winner_and_probability(logged, home_id, away_id)
        return {
            "status": "scheduled",
            "team_a": home_id, "team_b": away_id, "seed_a": seed_a, "seed_b": seed_b,
            "predicted_winner": predicted_winner, "win_probability": win_probability,
        }

    def resolve_matchup(
        self, team_a: str, team_b: str | None, seed_a: int | None, seed_b: int | None,
        real_matchups: dict[frozenset, dict], storage, s3, predictions_table,
        current_ratings: dict[str, float], home_advantage: float,
    ) -> dict:
        """One bracket slot as a display row. Its "predicted_winner" (or
        "actual_winner" once final) is what advances to the next round. A
        bye (team_b None) advances team_a outright."""
        if team_b is None:
            return {
                "status": "projected", "team_a": team_a, "seed_a": seed_a, "team_b": None, "seed_b": None,
                "predicted_winner": team_a, "win_probability": 1.0,
            }
        real_event = real_matchups.get(frozenset((team_a, team_b)))
        if real_event is None:
            matchup = self.season_simulation.project_matchup(team_a, team_b, seed_a, seed_b, current_ratings, home_advantage)
            matchup["status"] = "projected"
            return matchup

        event_key_value = real_event["event_key"]
        home_id, away_id = _home_and_away(real_event)
        if real_event.get("status") == "completed":
            return completed_matchup_row(real_event, event_key_value, home_id, away_id, seed_a, seed_b, predictions_table)
        return self.scheduled_matchup_row(
            real_event, event_key_value, home_id, away_id, seed_a, seed_b, storage, s3, predictions_table,
        )

    def project_bracket_round(
        self, round_name: str, pairs: Iterable[BracketPair],
        real_matchups: dict[frozenset, dict], storage, s3, predictions_table,
        current_ratings: dict[str, float], home_advantage: float,
    ) -> tuple[dict, list[tuple[str, int | None]]]:
        """One round's display dict, plus [(advancing_team, its_seed), ...]
        for the next round to consume."""
        matchups = []
        advancing = []
        for team_a, team_b, seed_a, seed_b in pairs:
            matchup = self.resolve_matchup(
                team_a, team_b, seed_a, seed_b, real_matchups, storage, s3, predictions_table,
                current_ratings, home_advantage,
            )
            matchups.append(matchup)
            advancing.append(_advancing_team(matchup, team_a, seed_a, seed_b))
        return {"round": round_name, "matchups": matchups}, advancing
