"""
Sport-agnostic model-performance math: turns graded samples (what a model
predicted vs what happened) into the per-model record the Performance tab
renders. Nothing here knows about sports, tables or S3 -- each sport's own
extraction (library.performance.head_to_head, ...) builds the samples and
this module does the rest, so every sport shares one scorecard shape.

Two kinds of model:

  pick    A yes/no call -- who wins, does the golfer make the top 10. Graded
          as right/wrong; headline metric is accuracy. Bands group the
          predictions by how sure the model was (win pick: HIGH/MED/LOW by
          how far the win chance was from 50/50).
  chance  A yes/no probability -- top-10, podium, DNF. Graded as right/wrong at
          the 50% line (called it if we gave more than 50%); headline metric
          is accuracy. Bands group the predictions by the chance we gave, in
          fifths (0-20%, 20-40%, ...), and each band's bar is the share we
          called right.
  amount  A number -- margin, a team's score, a player's yards. Headline
          metric is the average miss. Bands split the season's predicted
          values into equal thirds (lowest to highest predicted), and each
          band's bar is the share of predictions that landed within the
          model's margin of error (its average miss at training). Each band
          (and the season) also carries `bias`, the average signed error, so
          the UI can say whether the model tended to miss high or low.

Fewer than MIN_BAND_SAMPLE predictions in a band is flagged `early` -- the
UI shows "too early" instead of a percentage that means nothing yet.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

MIN_BAND_SAMPLE = 5
RECENT_PERIODS = 6

# (tag, minimum edge over a coin flip) -- the same tiers ConfidencePill in
# the app's game cards uses (edge = |win probability - 0.5|), so the two
# always agree on what HIGH/MED/LOW mean.
WIN_PICK_TIERS = (("HIGH", 0.13), ("MED", 0.06), ("LOW", 0.0))
AMOUNT_TIERS = ("LOW", "MED", "HIGH")

KIND_PICK = "pick"
KIND_AMOUNT = "amount"
KIND_CHANCE = "chance"

BAND_CONFIDENCE = "confidence"
BAND_PREDICTED_AMOUNT = "predicted_amount"
BAND_PREDICTED_CHANCE = "predicted_chance"

# Edges of the stated-chance bands, and where each tier starts.
CHANCE_EDGES = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)


@dataclass(frozen=True)
class Period:
    """One reporting period -- a week for football and basketball, one event
    for PGA/F1. `key` sorts chronologically; `label` is the short chip text."""
    key: str
    label: str


@dataclass(frozen=True)
class PickSample:
    period: Period
    correct: bool
    edge: float  # how sure the model was: |win probability - 0.5|
    baseline_correct: bool  # would "always pick the home side" have been right?


@dataclass(frozen=True)
class AmountSample:
    period: Period
    predicted: float
    actual: float


@dataclass(frozen=True)
class ChanceSample:
    period: Period
    probability: float  # the chance we gave that it happens
    happened: bool


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _by_period(samples: list, value_of, open_period: "Period | None" = None) -> list[dict]:
    """Chronological [{"label", "value", "n"}] -- one entry per finished
    period. `open_period` (one with games still to play) is left out: it
    isn't "last week" yet. The season figures still count its graded games."""
    grouped: dict[Period, list[float]] = {}
    for sample in samples:
        if sample.period != open_period:
            grouped.setdefault(sample.period, []).append(value_of(sample))
    return [
        {"label": period.label, "value": _mean(values), "n": len(values)}
        for period, values in sorted(grouped.items(), key=lambda item: item[0].key)
    ]


def _headline(periods: list[dict], season_value: float | None, season_n: int) -> dict:
    last = periods[-1] if periods else None
    return {
        "season": {"value": season_value, "n": season_n},
        "last_period": last,
        "periods": periods[-RECENT_PERIODS:],
    }


def _base(model_name: str, version: int | None, kind: str, band_kind: str, count_noun: str | None = None) -> dict:
    """`count_noun` ("golfers", "drivers") lets a sport name what one graded
    prediction is; when None the app falls back to its own per-model default."""
    record = {"model_name": model_name, "version": version, "kind": kind, "band_kind": band_kind}
    if count_noun is not None:
        record["count_noun"] = count_noun
    return record


def pick_record(
    model_name: str, version: int | None, samples: list[PickSample], at_training: float | None,
    tiers: tuple[tuple[str, float], ...] = WIN_PICK_TIERS, count_noun: str | None = None,
    open_period: Period | None = None,
) -> dict:
    record = _base(model_name, version, KIND_PICK, BAND_CONFIDENCE, count_noun)
    accuracy = _mean([1.0 if s.correct else 0.0 for s in samples])
    baseline = _mean([1.0 if s.baseline_correct else 0.0 for s in samples])
    record.update(_headline(_by_period(samples, lambda s: 1.0 if s.correct else 0.0, open_period), accuracy, len(samples)))
    record["vs_baseline_pct"] = (accuracy - baseline) / baseline * 100 if accuracy is not None and baseline else None
    record["at_training"] = at_training
    record["margin_of_error"] = None
    record["bands"] = [_pick_band(tag, floor, tiers, samples) for tag, floor in tiers]
    return record


def _pick_band(tag: str, floor: float, tiers, samples: list[PickSample]) -> dict:
    ceiling = min((other_floor for _, other_floor in tiers if other_floor > floor), default=None)
    members = [s for s in samples if s.edge >= floor and (ceiling is None or s.edge < ceiling)]
    return _band(tag, len(members), sum(1 for s in members if s.correct), lo=floor, hi=ceiling)


def _band(tag: str, n: int, hits: int, lo: float | None, hi: float | None, bias: float | None = None) -> dict:
    """`bias` (amount bands only) is the average of prediction minus actual for
    the band: positive means the model tended to miss high, negative low."""
    early = n < MIN_BAND_SAMPLE
    return {"tag": tag, "lo": lo, "hi": hi, "n": n, "pct": None if early else hits / n, "early": early, "bias": None if early else bias}


def _bias(samples: list["AmountSample"]) -> float | None:
    """Average signed error (prediction minus actual): + = missed high, - = low."""
    return _mean([s.predicted - s.actual for s in samples])


def amount_record(
    model_name: str, version: int | None, samples: list[AmountSample], at_training: float | None,
    margin_of_error: float | None, count_noun: str | None = None, open_period: Period | None = None,
) -> dict:
    """`margin_of_error` is the model's usual miss (its mean absolute error at
    training). Without one -- an older model card -- the season's own average
    miss stands in for it."""
    record = _base(model_name, version, KIND_AMOUNT, BAND_PREDICTED_AMOUNT, count_noun)
    misses = [abs(s.predicted - s.actual) for s in samples]
    avg_miss = _mean(misses)
    record.update(_headline(_by_period(samples, lambda s: abs(s.predicted - s.actual), open_period), avg_miss, len(samples)))

    actual_mean = _mean([s.actual for s in samples])
    baseline_miss = _mean([abs(s.actual - actual_mean) for s in samples]) if samples else None
    record["vs_baseline_pct"] = (baseline_miss - avg_miss) / baseline_miss * 100 if avg_miss is not None and baseline_miss else None
    record["at_training"] = at_training
    tolerance = margin_of_error if margin_of_error is not None else avg_miss
    record["margin_of_error"] = tolerance
    record["bias"] = _bias(samples)
    record["bands"] = _amount_bands(samples, tolerance)
    return record


def _amount_bands(samples: list[AmountSample], tolerance: float | None) -> list[dict]:
    """LOW/MED/HIGH: the season's predicted values split into equal-width
    thirds between the lowest and highest prediction seen."""
    if not samples or tolerance is None:
        return []
    lowest = min(s.predicted for s in samples)
    highest = max(s.predicted for s in samples)
    if highest == lowest:
        return []
    width = (highest - lowest) / 3
    edges = [lowest, lowest + width, lowest + 2 * width, highest]
    bands = []
    for index, tag in enumerate(AMOUNT_TIERS):
        lo, hi = edges[index], edges[index + 1]
        last = index == len(AMOUNT_TIERS) - 1
        members = [s for s in samples if s.predicted >= lo and (s.predicted <= hi if last else s.predicted < hi)]
        hits = sum(1 for s in members if abs(s.predicted - s.actual) <= tolerance)
        bands.append(_band(tag, len(members), hits, lo=lo, hi=hi, bias=_bias(members)))
    return bands


def chance_record(
    model_name: str, version: int | None, samples: list[ChanceSample], at_training: float | None, count_noun: str | None = None,
) -> dict:
    """A yes/no probability model. Accuracy is how often the call (more than
    50% = yes) was right; the baseline is always answering no, which is
    already right most of the time for a rare outcome like a top-10 finish."""
    record = _base(model_name, version, KIND_CHANCE, BAND_PREDICTED_CHANCE, count_noun)
    correct = [1.0 if (s.probability >= 0.5) == s.happened else 0.0 for s in samples]
    accuracy = _mean(correct)
    baseline = _mean([0.0 if s.happened else 1.0 for s in samples])
    grouped: dict[Period, list[float]] = {}
    for sample, hit in zip(samples, correct):
        grouped.setdefault(sample.period, []).append(hit)
    periods = [{"label": p.label, "value": _mean(v), "n": len(v)} for p, v in sorted(grouped.items(), key=lambda item: item[0].key)]
    record.update(_headline(periods, accuracy, len(samples)))
    record["vs_baseline_pct"] = (accuracy - baseline) / baseline * 100 if accuracy is not None and baseline else None
    record["at_training"] = at_training
    record["margin_of_error"] = None
    record["bands"] = _chance_bands(samples)
    return record


def _chance_bands(samples: list[ChanceSample]) -> list[dict]:
    bands = []
    for index in range(len(CHANCE_EDGES) - 1):
        lo, hi = CHANCE_EDGES[index], CHANCE_EDGES[index + 1]
        last = index == len(CHANCE_EDGES) - 2
        members = [s for s in samples if s.probability >= lo and (s.probability <= hi if last else s.probability < hi)]
        called_right = sum(1 for s in members if (s.probability >= 0.5) == s.happened)
        tag = "HIGH" if lo >= 0.6 else "MED" if lo >= 0.4 else "LOW"
        bands.append(_band(tag, len(members), called_right, lo=lo, hi=hi))
    return bands


def build_scorecard(sport: str, season: int | None, period_kind: str, records: list[dict]) -> dict:
    """The document stored at model-performance/{sport}/latest.json.
    `period_kind` ("week" | "event") tells the UI what "last period" means."""
    return {
        "sport": sport,
        "season": season,
        "period_kind": period_kind,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "models": records,
    }
