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

from library.aws.s3_manager import S3Manager
from library.ml import training_common
from library.ml.model_types import ModelAdapter

logger = logging.getLogger("model-training")


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
    except Exception:  # noqa: BLE001 -- cleanup best-effort, see docstring
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


def run_backtest(
    s3: S3Manager,
    sport: str,
    model_name: str,
    task: str,
    X_train, y_train, X_test, y_test,
    candidates: list[ModelAdapter],
    naive_baseline_metrics: dict,
    extra_metadata: dict,
    summary_metrics: list[str],
    promotion_metric: str,
    run_id: str,
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

    Every candidate gets tuned and fit on the identical holdout split, and
    every candidate is compared against whatever's currently hosted right
    now (training_common.would_beat_current/promote_if_better both read
    the live current.json pointer fresh, never a cached value from
    earlier in this run) the moment it finishes, in candidate list order.
    Only replaces what's live if it's genuinely at least as good (no
    percentage tolerance). An earlier candidate's own win in this same
    run is a real comparison target for a later one, same as a previous
    month's production version would be.

    A candidate that doesn't win is never persisted to S3 at all -- but its
    result is still real signal about this run, so whichever card is
    currently live (if this run has promoted anything yet) gets refreshed
    with it immediately via training_common.update_promoted_candidates,
    right after that candidate's would_beat_current comparison resolves.
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
    display_metric = "accuracy" if task == "classification" else "mae"

    progress = training_common.load_run_progress(s3, sport, model_name, run_id)
    if progress is None:
        evaluated = []
        promotions = []
    else:
        evaluated = progress["evaluated"]
        promotions = progress["promotions"]
        logger.info(
            "Resuming %s/%s run %s -- %d candidate(s) already settled by an earlier attempt.",
            sport, model_name, run_id, len(evaluated),
        )
    already_evaluated = {entry["algorithm"] for entry in evaluated}

    for adapter in candidates:
        if adapter.algorithm in already_evaluated:
            logger.info(
                "Skipping %s/%s candidate %s -- already settled by an earlier attempt of run %s.",
                sport, model_name, adapter.algorithm, run_id,
            )
            continue

        logger.info("Tuning and fitting %s/%s candidate: %s", sport, model_name, adapter.algorithm)
        tune_and_fit_started = time.perf_counter()
        try:
            estimator, best_params = adapter.tune_and_fit(X_train, y_train)
            training_seconds = time.perf_counter() - tune_and_fit_started
            predictions = adapter.predict(estimator, X_test)
            if task == "classification":
                metrics = training_common.evaluate_holdout(predictions, y_test)
            elif task == "regression":
                metrics = training_common.evaluate_regression_holdout(predictions, y_test)
            else:
                raise ValueError(f"Unknown task: {task!r} (expected 'classification' or 'regression')")

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
                "hyperparameters": best_params,
                "candidates": ranked_so_far,
                "candidates_ranked_by": promotion_metric,
            }

            if not training_common.would_beat_current(s3, sport, model_name, metadata, promotion_metric):
                logger.info(
                    "%s/%s candidate %s did not beat current production -- not persisted, moving to next candidate.",
                    sport, model_name, adapter.algorithm,
                )
                training_common.save_run_progress(s3, sport, model_name, run_id, evaluated, promotions)
                if promotions:
                    # A losing candidate is still real signal about this run --
                    # keep whichever card is currently live refreshed with it
                    # immediately, rather than only at promotion time or (worse)
                    # only if this function's own end-of-run backfill is ever
                    # reached at all.
                    training_common.update_promoted_candidates(
                        s3, sport, model_name, promotions[-1]["version"], ranked_so_far, promotion_metric,
                    )
                continue

            worse_than_baseline = _is_worse_than_baseline(metadata, naive_baseline_metrics, promotion_metric)
            if worse_than_baseline:
                logger.warning(
                    "%s/%s candidate %s is about to be promoted despite scoring WORSE than the naive baseline "
                    "on %s -- likely means this target isn't learnable with current features/data, not "
                    "necessarily a training bug. Promoting anyway (still beats/ties current production).",
                    sport, model_name, adapter.algorithm, promotion_metric,
                )

            card = training_common.save_model_artifact(
                s3, sport, model_name, adapter.algorithm,
                adapter.serialize(estimator), adapter.artifact_filename,
                metadata, summary_metrics,
            )
            training_common.promote_if_better(s3, sport, model_name, card["version"], metadata, promotion_metric)
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
    # whichever card was live at the time (the would_beat_current branch
    # above for a loss, save_model_artifact's own metadata for a win), so
    # there's nothing left to backfill here. Just keep the in-memory
    # return value consistent with what's now in S3, for any caller that
    # reads promotions[-1] directly rather than re-fetching the card.
    final_candidates = _full_candidate_summary(candidates, evaluated)
    if promotions:
        promotions[-1]["candidates"] = final_candidates
        promotions[-1]["candidates_ranked_by"] = promotion_metric

    training_common.clear_run_progress(s3, sport, model_name, run_id)
    return {"promotions": promotions, "candidates": final_candidates}
