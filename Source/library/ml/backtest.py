"""
Runs several candidate algorithms (library.ml.model_types.ModelAdapter
instances) against the same train/holdout split for one prediction
target, promoting incrementally as each one finishes rather than only at
the end.

Every target-specific concern (which columns are features, how the label
is derived, what a trivial/naive baseline looks like for this target) is
the caller's job -- a train_*.py script builds X_train/y_train/X_test/
y_test and its own naive_baseline_metrics, then hands them here with a
list of candidates to try. This module only knows how to run a fair
tournament among whatever candidates it's given and hand each one to
training_common's promotion helpers as it finishes.
"""
import gc
import logging
import time
from typing import Any, NamedTuple

import numpy as np
import pandas as pd

from library.aws.s3_manager import S3Manager
from library.features.common import DEFAULT_ROLLING_WINDOW
from library.ml import calibration, training_common
from library.ml import target_baseline as target_baseline_module
from library.ml.model_types import ModelAdapter
from library.serving import model_loader

logger = logging.getLogger("model-training")


class HoldoutSplit(NamedTuple):
    """Bundles the 4 train/test arrays run_backtest needs
    into a single parameter -- keeping them as 4 separate ones pushed both
    functions 1 over SonarQube's 13-parameter limit."""
    X_train: Any
    y_train: Any
    X_test: Any
    y_test: Any


def _release_candidate_resources() -> None:
    """Every candidate's tune_and_fit uses a joblib/loky-backed search
    (n_jobs=-1 or similar -- see model_types.py), and loky deliberately
    keeps its worker-process pool alive and warm between Parallel() calls
    for reuse rather than tearing it down -- efficient for back-to-back
    searches within one candidate, but it means a memory-heavy candidate
    (a large RandomForest ensemble was a real, live OOM trigger,
    2026-08-22) can leave its workers, and whatever they had
    memory-mapped, still resident when the next candidate in this same
    run_backtest loop starts. gc.collect() alone doesn't reach those
    subprocesses; explicitly shutting down loky's reusable executor does,
    at the cost of the next candidate paying a fresh process-pool spin-up
    (milliseconds, not worth avoiding for the memory headroom it buys).
    Best-effort: loky is joblib's own bundled copy, not a version-pinned
    dependency of ours, so a shape change there degrades to "skip the
    explicit release, rely on gc.collect() alone" rather than crashing
    the whole training run over cleanup."""
    gc.collect()
    try:
        from joblib.externals.loky import get_reusable_executor
        get_reusable_executor().shutdown(wait=True, kill_workers=True)
    except Exception:  # noqa: BLE001
        # Cleanup best-effort, see docstring.
        logger.debug("Could not shut down loky's reusable executor -- continuing without it.", exc_info=True)


def _full_candidate_summary(candidates: list[ModelAdapter], evaluated: list[dict]) -> list[dict]:
    """Every candidate in `candidates`, not just the ones evaluated so
    far. update_promoted_candidates's end-of-run backfill only fires if
    run_backtest's loop finishes cleanly -- a Fargate task killed or timed
    out partway through (a real risk this project already sized XGBoost's
    iteration cap and per-sport Fargate vCPU around) never reaches it, so
    a card written mid-run needs to already be complete on its own.
    Candidates not yet evaluated get a None-valued placeholder entry
    rather than being omitted, so a promoted-early winner never hides
    what else was even being considered for this target. Called both
    from inside run_backtest's loop (every card write) and for the
    final, fully-evaluated summary -- same shape either way, just with
    fewer placeholders left by the time the loop finishes normally."""
    evaluated_by_algorithm = {entry["algorithm"]: entry for entry in evaluated}
    summary = [
        evaluated_by_algorithm[adapter.algorithm] if adapter.algorithm in evaluated_by_algorithm else {
            "algorithm": adapter.algorithm,
            "score": None,
            "rank_score": None,
            "training_seconds": None,
            "status": "not_evaluated",
        }
        for adapter in candidates
    ]
    # Evaluated candidates ranked best-first by rank_score; not-yet-
    # evaluated ones (rank_score=None) sort last rather than raising.
    return sorted(summary, key=lambda e: (e["rank_score"] is None, e["rank_score"]))


def _is_worse_than_baseline(metadata: dict, naive_baseline_metrics: dict, promotion_metric: str) -> bool | None:
    """None if there's no baseline value comparable to promotion_metric at
    all, True/False otherwise. A lower-is-better metric (rmse/log_loss)
    compares directly against its own naive_baseline_<metric> counterpart
    when one exists; log_loss falls back to accuracy vs.
    naive_baseline_accuracy when its own baseline isn't available.
    Warn-only, never blocks promotion."""
    baseline_key = f"naive_baseline_{promotion_metric}"
    if baseline_key in naive_baseline_metrics:
        return metadata[promotion_metric] > naive_baseline_metrics[baseline_key]
    if promotion_metric == "log_loss" and "naive_baseline_accuracy" in naive_baseline_metrics and "accuracy" in metadata:
        return metadata["accuracy"] < naive_baseline_metrics["naive_baseline_accuracy"]
    return None


CHAMPION_RESCORED = "rescored"
CHAMPION_STORED = "stored"
CHAMPION_THIS_RUN = "this_run"


class RunOptions(NamedTuple):
    """Optional per-run training choices: an Elo-implied target baseline a
    regressor learns the correction to, and per-row training weights that
    each candidate is also refit with once."""
    target_baseline: dict | None = None
    sample_weights: Any = None


class Champion(NamedTuple):
    """What a candidate must beat: `score` on the promotion metric, and how it
    was obtained (one of the CHAMPION_* bases)."""
    version: int
    score: float
    basis: str


def _rescore(estimator: Any, card: dict, task: str, split: HoldoutSplit, metric: str, test_start: str | None) -> float | None:
    """The production model's metric on this run's holdout, or None when it
    trained on any holdout date or lacks a column the holdout has."""
    trained_through = (card.get("train_date_range") or [None, None])[1]
    if trained_through is None or test_start is None or trained_through >= test_start:
        return None
    columns = card["feature_columns"]
    if any(column not in split.X_test.columns for column in columns):
        return None
    predictions = model_loader.card_predictions(estimator, card, split.X_test[columns])
    return _evaluate_for_task(task, predictions, split.y_test)[metric]


def _current_champion(
    s3: S3Manager, sport: str, model_name: str, task: str, split: HoldoutSplit, metric: str, test_start: str | None,
) -> Champion | None:
    version = training_common.get_current_version(s3, sport, model_name)
    if version is None:
        return None
    card = training_common.load_model_card(s3, sport, model_name, version)
    try:
        estimator, _ = model_loader.load_current_model(s3, sport, model_name)
        score = _rescore(estimator, card, task, split, metric, test_start)
    except Exception:  # noqa: BLE001
        logger.warning("Could not re-score %s/%s v%d on this holdout.", sport, model_name, version, exc_info=True)
        score = None
    champion = Champion(version, card[metric], CHAMPION_STORED) if score is None else Champion(version, score, CHAMPION_RESCORED)
    logger.info("%s/%s champion: v%d %s=%.5f (%s).", sport, model_name, version, metric, champion.score, champion.basis)
    return champion


def _promotion_decision(champion: Champion | None, score: float, metric: str) -> dict:
    if champion is None:
        return {"promoted": True, "challenger": score}
    return {
        "promoted": training_common.beats(score, champion.score, metric),
        "challenger": score,
        "champion_version": champion.version,
        "champion": champion.score,
        "champion_basis": champion.basis,
        "required_margin": training_common.PROMOTION_MARGINS.get(metric, 0.0),
    }


# Most recent share of the training rows a classifier's calibration is fitted on.
CALIBRATION_FRACTION = 0.2


def _fit_calibration(adapter: ModelAdapter, params: dict, X_train: Any, y_train: Any, weights: Any = None) -> dict | None:
    """Platt scaling fitted on the most recent CALIBRATION_FRACTION of the
    training rows, predicted by a refit on the rest with the same params."""
    cut = int(len(X_train) * (1 - CALIBRATION_FRACTION))
    try:
        estimator = adapter.fit(X_train.iloc[:cut], y_train.iloc[:cut], params, None if weights is None else weights[:cut])
        return calibration.fit_platt(adapter.predict(estimator, X_train.iloc[cut:]), y_train.iloc[cut:])
    except Exception:  # noqa: BLE001
        logger.warning("Could not fit a calibration for %s -- keeping it uncalibrated.", adapter.algorithm, exc_info=True)
        return None


def _calibrated(
    adapter: ModelAdapter, params: dict, split: HoldoutSplit, predictions: Any, metrics: dict, promotion_metric: str,
    weights: Any = None,
) -> tuple[Any, dict, dict | None]:
    """(predictions, metrics, calibration): the calibrated versions when they
    improve promotion_metric on the holdout, else the inputs and None."""
    fitted = _fit_calibration(adapter, params, split.X_train, split.y_train, weights)
    if fitted is None:
        return predictions, metrics, None
    calibrated = calibration.apply(fitted, predictions)
    calibrated_metrics = _evaluate_for_task("classification", calibrated, split.y_test)
    if calibrated_metrics[promotion_metric] < metrics[promotion_metric]:
        return calibrated, calibrated_metrics, fitted
    return predictions, metrics, None


def _recency_weighted(
    adapter: ModelAdapter, params: dict, split: HoldoutSplit, test_offset: Any, weights: Any,
    task: str, promotion_metric: str, fitted: tuple[Any, Any, dict],
) -> tuple[Any, Any, dict, bool]:
    """(estimator, predictions, metrics, weighted): a refit with `weights`
    when it improves promotion_metric on the holdout, else `fitted` as given."""
    estimator, predictions, metrics = fitted
    try:
        weighted = adapter.fit(split.X_train, split.y_train, params, weights)
    except Exception:  # noqa: BLE001
        logger.warning("Could not refit %s with recency weights -- keeping it unweighted.", adapter.algorithm, exc_info=True)
        return estimator, predictions, metrics, False
    weighted_predictions = adapter.predict(weighted, split.X_test) + test_offset
    weighted_metrics = _evaluate_for_task(task, weighted_predictions, split.y_test)
    if weighted_metrics[promotion_metric] < metrics[promotion_metric]:
        return weighted, weighted_predictions, weighted_metrics, True
    return estimator, predictions, metrics, False


class _CandidateFit(NamedTuple):
    estimator: Any
    params: dict
    predictions: Any
    metrics: dict
    training_seconds: float
    calibration: dict | None
    recency_weighted: bool


def _fit_candidate(
    adapter: ModelAdapter, task: str, fit_split: HoldoutSplit, split: HoldoutSplit, test_offset: Any,
    options: RunOptions, promotion_metric: str,
) -> _CandidateFit:
    """Tunes and fits one candidate on fit_split (labels net of any target
    baseline), then keeps a recency-weighted refit and a calibration where
    each improves promotion_metric on the holdout."""
    started = time.perf_counter()
    estimator, params = adapter.tune_and_fit(fit_split.X_train, fit_split.y_train)
    training_seconds = time.perf_counter() - started
    predictions = adapter.predict(estimator, fit_split.X_test) + test_offset
    metrics = _evaluate_for_task(task, predictions, fit_split.y_test)
    weighted = False
    if options.sample_weights is not None:
        estimator, predictions, metrics, weighted = _recency_weighted(
            adapter, params, fit_split, test_offset, options.sample_weights, task, promotion_metric,
            (estimator, predictions, metrics),
        )
    fitted_calibration = None
    if task == "classification":
        predictions, metrics, fitted_calibration = _calibrated(
            adapter, params, split, predictions, metrics, promotion_metric, options.sample_weights if weighted else None,
        )
    return _CandidateFit(estimator, params, predictions, metrics, training_seconds, fitted_calibration, weighted)


def _season_stage(X: pd.DataFrame) -> pd.Series | None:
    """This season's games behind each row (the fewer side's for an event row),
    or None when the rows don't carry it."""
    if "games_this_season" in X.columns:
        return X["games_this_season"]
    sides = ["home_games_this_season", "away_games_this_season"]
    return X[sides].min(axis=1, skipna=False) if set(sides) <= set(X.columns) else None


def _holdout_by_season_stage(task: str, predictions: Any, split: HoldoutSplit) -> dict | None:
    """Holdout metrics for rows whose rolling window isn't yet full of this
    season's games, and for the rest."""
    stage = _season_stage(split.X_test)
    if stage is None:
        return None
    by_stage = {}
    for name, mask in (("early_season", stage < DEFAULT_ROLLING_WINDOW), ("rest_of_season", stage >= DEFAULT_ROLLING_WINDOW)):
        rows = mask.to_numpy()
        if rows.any():
            by_stage[name] = {"rows": int(rows.sum()), **_evaluate_for_task(task, np.asarray(predictions)[rows], split.y_test[rows])}
    return by_stage


def _load_resumed_progress(s3: S3Manager, sport: str, model_name: str, run_id: str) -> tuple[list[dict], list[dict]]:
    """(evaluated, promotions) left by an earlier attempt of run_id, or two
    empty lists for a fresh run."""
    progress = training_common.load_run_progress(s3, sport, model_name, run_id)
    if progress is None:
        return [], []
    logger.info(
        "Resuming %s/%s run %s -- %d candidate(s) already settled by an earlier attempt.",
        sport, model_name, run_id, len(progress["evaluated"]),
    )
    return progress["evaluated"], progress["promotions"]


def _evaluate_for_task(task: str, predictions: Any, y_test: Any) -> dict:
    if task == "classification":
        return training_common.evaluate_holdout(predictions, y_test)
    if task == "regression":
        return training_common.evaluate_regression_holdout(predictions, y_test)
    raise ValueError(f"Unknown task: {task!r} (expected 'classification' or 'regression')")


def _record_losing_candidate(
    s3: S3Manager,
    sport: str,
    model_name: str,
    run_id: str,
    algorithm: str,
    evaluated: list[dict],
    promotions: list[dict],
    ranked_so_far: list[dict],
    promotion_metric: str,
) -> None:
    logger.info(
        "%s/%s candidate %s did not beat the champion by the required margin -- not persisted, moving to next candidate.",
        sport, model_name, algorithm,
    )
    training_common.save_run_progress(s3, sport, model_name, run_id, evaluated, promotions)
    if promotions:
        # A losing candidate is still real signal about this run -- keep
        # whichever card is currently live refreshed with it immediately,
        # rather than only at promotion time or (worse) only if
        # run_backtest's own end-of-run backfill is ever reached at all.
        training_common.update_promoted_candidates(
            s3, sport, model_name, promotions[-1]["version"], ranked_so_far, promotion_metric,
        )


def _warn_if_worse_than_baseline(
    sport: str, model_name: str, algorithm: str, metadata: dict, naive_baseline_metrics: dict, promotion_metric: str,
) -> None:
    if _is_worse_than_baseline(metadata, naive_baseline_metrics, promotion_metric):
        logger.warning(
            "%s/%s candidate %s is about to be promoted despite scoring WORSE than the naive baseline "
            "on %s -- likely means this target isn't learnable with current features/data, not "
            "necessarily a training bug. Promoting anyway (still beats the champion).",
            sport, model_name, algorithm, promotion_metric,
        )


def run_backtest(
    s3: S3Manager,
    sport: str,
    model_name: str,
    task: str,
    split: HoldoutSplit,
    candidates: list[ModelAdapter],
    naive_baseline_metrics: dict,
    extra_metadata: dict,
    summary_metrics: list[str],
    promotion_metric: str,
    run_id: str,
    options: RunOptions = RunOptions(),
) -> dict:
    """task: "classification" or "regression" -- decides whether holdout
    metrics come from evaluate_holdout (accuracy/log_loss) or
    evaluate_regression_holdout (rmse/mae). naive_baseline_metrics: the
    target's own trivial-baseline numbers (e.g. {"naive_baseline_accuracy":
    0.57} for a classifier, {"naive_baseline_rmse": ..., "naive_baseline_mae":
    ...} for a regressor), computed once by the caller then merged onto
    every candidate's model card identically. run_id: see
    training_common.resolve_run_id -- identifies this run's own
    resumable-progress breadcrumb, so a task that gets interrupted
    mid-tournament and relaunched with the same run_id picks up where it
    left off instead of redoing already-decided candidates.

    Every candidate gets tuned and fit on the identical holdout split and is
    compared, in candidate list order, against the champion: the production
    model re-scored once on this holdout (its stored score when it can't be),
    or an earlier winner of this run. A candidate must beat it by
    training_common.PROMOTION_MARGINS; the decision is written to the
    winner's card as promotion_decision.

    A candidate that doesn't win is never persisted to S3 at all -- but its
    result is still real signal about this run, so whichever card is
    currently live (if this run has promoted anything yet) gets refreshed
    with it immediately via training_common.update_promoted_candidates,
    right after that candidate's promotion decision resolves.
    Combined with every `candidates` summary already listing every
    algorithm in `candidates` -- win, lose, or not yet reached, the latter
    as a None/"not_evaluated" placeholder rather than being left off (see
    _full_candidate_summary) -- this means whichever card is live is kept
    fully self-complete after every single candidate, not just once this
    function finishes. That matters because a task killed or timed out
    mid-run (a real risk this project already sizes Fargate vCPU and
    XGBoost's iteration cap around) never gets to run any end-of-function
    cleanup at all -- whatever was live at the moment it died already
    shows the complete field being considered, scores rolled in as each
    candidate finished, not a snapshot frozen at promotion time. Ranked
    best-first by promotion_metric but displaying accuracy
    (classification) or mae (regression) instead, which is human-readable
    unlike log_loss/rmse. A winning candidate whose promotion_metric is
    worse than the naive baseline gets a loud warning logged (see
    _is_worse_than_baseline) but is not blocked.

    Progress (which candidates have been evaluated and their scores) is
    written to S3 after every single candidate -- win or lose -- via
    training_common.save_run_progress, so a task interrupted between any
    two candidates resumes without redoing either the tuning/fitting or
    the promotion decision for candidates already settled. The breadcrumb
    is deleted (training_common.clear_run_progress) once every candidate
    in the list has been evaluated.

    Returns {"promotions": [model_card, ...], "candidates": [summary, ...]}
    -- promotions lists, in the order they happened across the whole run,
    every card that actually went live (usually 0 or 1; occasionally
    more, if a later candidate beats an earlier one that itself just
    won); top-level candidates is the full score summary of every
    algorithm tried this run, win or lose.
    """
    X_train, y_train, X_test, y_test = split
    test_offset = target_baseline_module.apply(options.target_baseline, X_test)
    fit_split = HoldoutSplit(X_train, y_train - target_baseline_module.apply(options.target_baseline, X_train), X_test, y_test)
    display_metric = "accuracy" if task == "classification" else "mae"

    evaluated, promotions = _load_resumed_progress(s3, sport, model_name, run_id)
    already_evaluated = {entry["algorithm"] for entry in evaluated}
    if promotions:
        champion = Champion(promotions[-1]["version"], promotions[-1][promotion_metric], CHAMPION_THIS_RUN)
    else:
        test_start = (extra_metadata.get("test_date_range") or [None])[0]
        champion = _current_champion(s3, sport, model_name, task, split, promotion_metric, test_start)

    for adapter in candidates:
        if adapter.algorithm in already_evaluated:
            logger.info(
                "Skipping %s/%s candidate %s -- already settled by an earlier attempt of run %s.",
                sport, model_name, adapter.algorithm, run_id,
            )
            continue

        logger.info("Tuning and fitting %s/%s candidate: %s", sport, model_name, adapter.algorithm)
        try:
            fit = _fit_candidate(adapter, task, fit_split, split, test_offset, options, promotion_metric)
            estimator, metrics, training_seconds = fit.estimator, fit.metrics, fit.training_seconds

            logger.info(
                "%s/%s candidate %s: %s (training_seconds=%.1f)", sport, model_name, adapter.algorithm,
                " ".join(f"{k}={v:.4f}" for k, v in metrics.items()), training_seconds,
            )
            # "score" is the display metric, not promotion_metric. rank_score
            # carries promotion_metric's own value alongside it so a
            # candidate with the best "score" not winning doesn't look like a
            # bug (candidates_ranked_by, alongside the list, names which
            # metric rank_score is). training_seconds is wall-clock time for
            # tune_and_fit alone, not predict/evaluate.
            evaluated.append({
                "algorithm": adapter.algorithm,
                "score": metrics[display_metric],
                "rank_score": metrics[promotion_metric],
                "training_seconds": training_seconds,
            })
            ranked_so_far = _full_candidate_summary(candidates, evaluated)

            metadata = {
                **extra_metadata,
                **metrics,
                "training_seconds": training_seconds,
                **naive_baseline_metrics,
                # The exact column order/selection model_loader.predict()
                # needs to build a live feature_row into what the estimator
                # was actually trained on.
                "feature_columns": list(X_train.columns),
                "feature_importances": adapter.feature_importances(estimator, list(X_train.columns)),
                "holdout_by_season_stage": _holdout_by_season_stage(task, fit.predictions, split),
                "hyperparameters": fit.params,
                "calibration": fit.calibration,
                "target_baseline": options.target_baseline,
                "recency_weighted": fit.recency_weighted,
                "candidates": ranked_so_far,
                "candidates_ranked_by": promotion_metric,
            }

            decision = _promotion_decision(champion, metrics[promotion_metric], promotion_metric)
            if not decision["promoted"]:
                _record_losing_candidate(
                    s3, sport, model_name, run_id, adapter.algorithm,
                    evaluated, promotions, ranked_so_far, promotion_metric,
                )
                continue

            _warn_if_worse_than_baseline(
                sport, model_name, adapter.algorithm, metadata, naive_baseline_metrics, promotion_metric,
            )

            card = training_common.save_model_artifact(
                s3, sport, model_name, adapter.algorithm,
                adapter.serialize(estimator), adapter.artifact_filename,
                {**metadata, "promotion_decision": decision}, summary_metrics,
            )
            training_common.set_current_version(s3, sport, model_name, card["version"])
            champion = Champion(card["version"], metrics[promotion_metric], CHAMPION_THIS_RUN)
            promotions.append(card)
            training_common.save_run_progress(s3, sport, model_name, run_id, evaluated, promotions)
            logger.info("%s/%s: %s (v%d) is now live.", sport, model_name, adapter.algorithm, card["version"])
        finally:
            # Runs whether this candidate won, lost, or raised -- a
            # memory-heavy candidate's fitted estimator and its search's
            # worker pool shouldn't still be resident once the next
            # candidate in this same process starts (see
            # _release_candidate_resources's own docstring). Doesn't
            # apply across a genuine restart (a new Fargate task is a
            # fresh container with nothing to release -- see this
            # function's own docstring on run_id/resumability); this is
            # for the several candidates that run back-to-back within one
            # attempt.
            _release_candidate_resources()

    # Every candidate's result -- win or lose -- was already written onto
    # whichever card was live at the time (_record_losing_candidate for a
    # loss, save_model_artifact's own metadata for a win), so
    # there's nothing left to backfill here. Just keep the in-memory
    # return value consistent with what's now in S3, for any caller that
    # reads promotions[-1] directly rather than re-fetching the card.
    final_candidates = _full_candidate_summary(candidates, evaluated)
    if promotions:
        promotions[-1]["candidates"] = final_candidates
        promotions[-1]["candidates_ranked_by"] = promotion_metric

    training_common.clear_run_progress(s3, sport, model_name, run_id)
    return {"promotions": promotions, "candidates": final_candidates}
