"""
Graded samples for the field-event sports (PGA, F1): one tournament or race
is one period, and each participant (golfer / driver) is one graded
prediction per model. Pure functions -- library.performance.runner does the
fetching.

Each sport lists the models it grades in a small spec table: what result
column a model is graded against, and whether it is a yes/no chance model
("will this golfer finish top 10") or an amount ("what will the winning
score be"). Everything after extraction is shared with the team sports via
library.performance.scorecard.

Every model is graded against the event's one pre-event snapshot -- for PGA that
snapshot also holds each round's forecast, made before the first tee.
"""
import re
from dataclasses import dataclass
from typing import Callable

from library.performance import scorecard
from library.performance.scorecard import AmountSample, ChanceSample, Period
from library.serving.prediction_snapshots import FINAL_PREGAME, pregame_rows

_FINISHED = "finished"


@dataclass(frozen=True)
class FieldModelSpec:
    kind: str  # scorecard.KIND_CHANCE or KIND_AMOUNT
    # Participant -> what the model is graded against (bool for a chance model,
    # number for an amount); None skips that participant.
    actual: Callable[[dict], float | bool | None] | None = None
    # For a model scored per something other than a participant (an F1
    # constructor): (event, entity id) -> bool | None.
    entity_actual: Callable[[dict, str], float | bool | None] | None = None


def _finish(participant: dict) -> int | None:
    return (participant.get("result") or {}).get("finish_position")


def _pga_top(n: int) -> Callable[[dict], bool | None]:
    def actual(participant: dict) -> bool | None:
        result = participant.get("result") or {}
        if result.get("status") is None:
            return None
        return result.get("status") == _FINISHED and result.get("finish_position") is not None and result["finish_position"] <= n
    return actual


def _pga_final_score(participant: dict) -> float | None:
    result = participant.get("result") or {}
    return result.get("score_to_par") if result.get("status") == _FINISHED else None


def _pga_round_score(round_number: int) -> Callable[[dict], float | None]:
    def actual(participant: dict) -> float | None:
        for round_result in (participant.get("result") or {}).get("rounds") or []:
            if round_result.get("round") == round_number:
                return round_result.get("score_to_par")
        return None
    return actual


def _f1_finish_within(n: int) -> Callable[[dict], bool | None]:
    def actual(participant: dict) -> bool | None:
        result = participant.get("result") or {}
        if result.get("status") is None:
            return None
        return result.get("finish_position") is not None and result["finish_position"] <= n
    return actual


def _f1_dnf(participant: dict) -> bool | None:
    status = (participant.get("result") or {}).get("status")
    return None if status is None else status == "dnf"


def _f1_qualifying_position(participant: dict) -> float | None:
    return ((participant.get("result") or {}).get("qualifying") or {}).get("position")


def _f1_grid_position(participant: dict) -> float | None:
    return (participant.get("result") or {}).get("grid_position")


PGA_SPECS: dict[str, FieldModelSpec] = {
    "top-10-probability": FieldModelSpec(scorecard.KIND_CHANCE, _pga_top(10)),
    "top-5-probability": FieldModelSpec(scorecard.KIND_CHANCE, _pga_top(5)),
    "projected-score-to-par": FieldModelSpec(scorecard.KIND_AMOUNT, _pga_final_score),
    **{f"round-{n}": FieldModelSpec(scorecard.KIND_AMOUNT, _pga_round_score(n)) for n in (1, 2, 3, 4)},
}

def _f1_constructor_won(event: dict, constructor_id: str) -> bool | None:
    """Did this constructor win the race? -- the winning driver's constructor."""
    winner = next((p for p in event.get("participants", []) if (p.get("result") or {}).get("finish_position") == 1), None)
    return None if winner is None else winner.get("constructor_entity_id") == constructor_id


F1_FIELD_SPECS: dict[str, FieldModelSpec] = {
    "win-probability": FieldModelSpec(scorecard.KIND_CHANCE, _f1_finish_within(1)),
    "podium-probability": FieldModelSpec(scorecard.KIND_CHANCE, _f1_finish_within(3)),
    "dnf-probability": FieldModelSpec(scorecard.KIND_CHANCE, _f1_dnf),
    "projected-finish-position": FieldModelSpec(scorecard.KIND_AMOUNT, _finish),
    "projected-qualifying-position": FieldModelSpec(scorecard.KIND_AMOUNT, _f1_qualifying_position),
    "constructor-win-probability": FieldModelSpec(scorecard.KIND_CHANCE, entity_actual=_f1_constructor_won),
}

F1_SPRINT_SPECS: dict[str, FieldModelSpec] = {
    "sprint-win-probability": FieldModelSpec(scorecard.KIND_CHANCE, _f1_finish_within(1)),
    "sprint-podium-probability": FieldModelSpec(scorecard.KIND_CHANCE, _f1_finish_within(3)),
    "projected-sprint-grid-position": FieldModelSpec(scorecard.KIND_AMOUNT, _f1_grid_position),
}

# sport -> (event_type -> specs, what one graded prediction is called)
SPORTS = {
    "pga": ({"field": PGA_SPECS}, "golfers"),
    "f1": ({"field": F1_FIELD_SPECS, "sprint": F1_SPRINT_SPECS}, "drivers"),
}
# A model scored per constructor counts constructors, not drivers.
COUNT_NOUN_OVERRIDES = {"constructor-win-probability": "constructors"}

_ROW_KEY = re.compile(r"^MODEL#(?P<model>[a-z0-9-]+)#v\d+#(?:GOLFER|DRIVER|CONSTRUCTOR)#(?P<entity>.+)$")


def period_for(event: dict) -> Period:
    """One period per event, ordered by date. The chip label is the event's
    name trimmed to its first two words ("Biltmore Championship")."""
    name = event.get("tournament_name") or event.get("race_name") or "Event"
    return Period(f"{event['event_date']}-{event['event_id']}", " ".join(name.split()[:2]))


def _predictions_by_entity(rows: list[dict], model_name: str) -> dict[str, dict]:
    """Latest row per participant for one model -- a model re-scored after a
    repromotion leaves one row per version."""
    latest: dict[str, dict] = {}
    for row in rows:
        match = _ROW_KEY.match(row["model_key"])
        if match is None or match.group("model") != model_name:
            continue
        entity = match.group("entity")
        if entity not in latest or row.get("generated_at", "") >= latest[entity].get("generated_at", ""):
            latest[entity] = row
    return latest


def collect_samples(sport: str, events: list[dict], raw_rows_by_event: dict[str, list[dict]]) -> dict[str, list]:
    """{model_name: [samples]} across every completed event of the sport."""
    specs_by_type, _ = SPORTS[sport]
    samples: dict[str, list] = {}
    for event in events:
        specs = specs_by_type.get(event.get("event_type"))
        raw_rows = raw_rows_by_event.get(event["event_key"], [])
        if specs is None or not raw_rows:
            continue
        period = period_for(event)
        participants = {p["entity_id"]: p for p in event.get("participants", [])}
        for model_name, spec in specs.items():
            rows, _ = pregame_rows(raw_rows, FINAL_PREGAME)
            for entity, row in _predictions_by_entity(rows, model_name).items():
                if spec.entity_actual is not None:
                    actual = spec.entity_actual(event, entity)
                else:
                    participant = participants.get(entity)
                    actual = spec.actual(participant) if participant is not None else None
                if actual is None:
                    continue
                predicted = row["predicted_value"]["value"]
                if spec.kind == scorecard.KIND_CHANCE:
                    samples.setdefault(model_name, []).append(ChanceSample(period, predicted, bool(actual), (entity,)))
                else:
                    samples.setdefault(model_name, []).append(AmountSample(period, predicted, actual, (entity,)))
    return samples


def build_records(sport: str, samples_by_model: dict[str, list], model_cards: list[dict]) -> list[dict]:
    """One record per promoted model this sport grades (a promoted model such as
    the PGA cutline, which has nothing here to grade against, is not reported)."""
    specs_by_type, noun = SPORTS[sport]
    specs = {name: spec for group in specs_by_type.values() for name, spec in group.items()}
    records = []
    for card in sorted(model_cards, key=lambda c: c["model_name"]):
        name = card["model_name"]
        spec = specs.get(name)
        if spec is None:
            continue
        samples = samples_by_model.get(name, [])
        # A constructor is a team entity; golfers and drivers are players.
        entity_type = "team" if spec.entity_actual is not None else "player"
        if spec.kind == scorecard.KIND_CHANCE:
            records.append(scorecard.chance_record(
                name, card.get("version"), samples, card.get("accuracy"), COUNT_NOUN_OVERRIDES.get(name, noun), entity_type=entity_type,
            ))
        else:
            mae = card.get("mae")
            records.append(scorecard.amount_record(name, card.get("version"), samples, mae, mae, noun, entity_type=entity_type))
    return records


def build_field_scorecard(
    sport: str, season: int | None, events: list[dict], raw_rows_by_event: dict[str, list[dict]], model_cards: list[dict],
) -> dict:
    samples = collect_samples(sport, events, raw_rows_by_event)
    return scorecard.build_scorecard(sport, season, "event", build_records(sport, samples, model_cards))
