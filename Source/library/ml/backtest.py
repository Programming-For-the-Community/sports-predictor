"""
Runs several candidate algorithms (library.ml.model_types.ModelAdapter
instances) against the same train/holdout split for one prediction
target, and promotes whichever one wins -- the shared backtesting harness
design/PROJECT_PLAN.md's Phase 4 calls for, generalized to run for any
sport/target/task rather than being written once per model script.

Every target-specific concern (which columns are features, how the label
is derived, what a trivial/naive baseline looks like for this target) is
the caller's job -- a train_*.py script builds X_train/y_train/X_test/
y_test and its own naive_baseline_metrics, then hands them here with a
list of candidates to try. This module only knows how to run a fair
tournament among whatever candidates it's given and hand the result to
training_common.promote_if_better -- it has no sport- or target-specific
knowledge at all.
"""
import logging

from library.aws.s3_manager import S3Manager
from library.ml import training_common
from library.ml.model_types import ModelAdapter

logger = logging.getLogger("model-training")


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
) -> dict:
    """task: "classification" or "regression" -- decides whether holdout
    metrics come from evaluate_holdout (accuracy/log_loss) or
    evaluate_regression_holdout (rmse/mae). naive_baseline_metrics: the
    target's own trivial-baseline numbers (e.g. {"naive_baseline_accuracy":
    0.57} for a classifier, {"naive_baseline_rmse": ..., "naive_baseline_mae":
    ...} for a regressor) -- computed once by the caller since what
    "naive" means is entirely target-specific (always predict home wins;
    predict this player's own rolling average; ...), then merged onto
    every candidate's model card identically so they're all compared
    against the same baseline.

    Every candidate gets tuned, fit, and evaluated on the identical
    holdout split, but only the WINNER (best promotion_metric) gets a
    versioned artifact actually written to S3 -- losing candidates are
    scored and compared in memory only, never persisted. Deliberately not
    "keep every candidate around for review": with 4-5 candidates per
    target and 11 targets retraining weekly, versioning every candidate
    would mean 40+ new S3 objects a week that are never read again (only
    the promoted -- or, on a held-back run, the single new best-scoring --
    version ever gets loaded by anything). The winning card still carries
    a `candidates` summary of every algorithm tried this run, ranked
    best-first by promotion_metric (the correct scoring rule for deciding
    a winner) but DISPLAYING each one's accuracy (classification) or mae
    (regression) instead -- human-readable, unlike log_loss/rmse -- so the
    one artifact that does get saved still shows what it competed against.
    Then promotes it via the existing, algorithm-agnostic
    training_common.promote_if_better, same as if only one algorithm had
    ever been tried.

    Returns {"winner": winner_card, "promoted": bool, "candidates": [summary, ...]}
    -- "candidates" here is the lightweight score summary (see above), not
    full model cards, since no other candidate has one.
    """
    evaluated = []
    for adapter in candidates:
        logger.info("Tuning and fitting %s/%s candidate: %s", sport, model_name, adapter.algorithm)
        estimator, best_params = adapter.tune_and_fit(X_train, y_train)
        predictions = adapter.predict(estimator, X_test)
        if task == "classification":
            metrics = training_common.evaluate_holdout(predictions, y_test)
        elif task == "regression":
            metrics = training_common.evaluate_regression_holdout(predictions, y_test)
        else:
            raise ValueError(f"Unknown task: {task!r} (expected 'classification' or 'regression')")

        evaluated.append({
            "adapter": adapter,
            "estimator": estimator,
            "hyperparameters": best_params,
            "metrics": metrics,
            "feature_importances": adapter.feature_importances(estimator, list(X_train.columns)),
        })
        logger.info(
            "%s/%s candidate %s: %s", sport, model_name, adapter.algorithm,
            " ".join(f"{k}={v:.4f}" for k, v in metrics.items()),
        )

    # "score" is accuracy (classification) or mae (regression), NOT
    # promotion_metric (log_loss/rmse) -- log_loss/rmse are the right rule
    # for DECIDING a winner (see the ranking below), but neither means
    # anything to someone reading the model card without an ML background,
    # the same reasoning model_card_view.dart's primary metrics already
    # apply. accuracy displays as a percentage; mae displays as a +/- error
    # range in the target's own units -- both easily digestible without
    # knowing what "log loss 0.65" or "RMSE 9.8" means. Ranked by the
    # actual gate metric (promotion_metric), not by the display metric, so
    # order is always best-first regardless of whether "best" means
    # highest (accuracy) or lowest (mae) -- the frontend just renders this
    # list in order, with no need to know or encode which direction is
    # better.
    #
    # rank_score carries promotion_metric's own value alongside "score" --
    # without it, a candidate with the highest displayed "score" not
    # winning looks like a bug (confirmed confusing in practice: a real
    # run promoted xgboost over a candidate with higher raw accuracy,
    # because xgboost had the better log_loss, which is what actually
    # decides -- correct, but invisible in the raw JSON before this
    # field existed). candidates_ranked_by, alongside the list rather than
    # repeated per-entry, names which metric rank_score is.
    display_metric = "accuracy" if task == "classification" else "mae"
    ranked = sorted(evaluated, key=lambda e: e["metrics"][promotion_metric])
    candidate_summary = [
        {
            "algorithm": e["adapter"].algorithm,
            "score": e["metrics"][display_metric],
            "rank_score": e["metrics"][promotion_metric],
        }
        for e in ranked
    ]

    # ranked[0] is the winner -- sorted by promotion_metric above, and
    # that sort never changes regardless of how many candidates there
    # are, so no separate "find the best one" step is needed here.
    winner = ranked[0]
    adapter = winner["adapter"]
    metadata = {
        **extra_metadata,
        **winner["metrics"],
        **naive_baseline_metrics,
        # The exact column order/selection model_loader.predict() (serving
        # side) needs to build a live feature_row into what the estimator
        # was actually trained on -- was missing from every model card this
        # produced until now (confirmed live: every prediction request
        # crashed with KeyError: 'feature_columns'), since none of the
        # three train_*.py callers passed it via extra_metadata and this
        # function itself never added it despite already having X_train in
        # scope for feature_importances just above. One fix here covers
        # every target -- X_train is identical across every candidate, so
        # capturing it once off the winner's own training data is correct
        # regardless of which candidate won.
        "feature_columns": list(X_train.columns),
        "feature_importances": winner["feature_importances"],
        "hyperparameters": winner["hyperparameters"],
        "candidates": candidate_summary,
        "candidates_ranked_by": promotion_metric,
    }
    winner_card = training_common.save_model_artifact(
        s3, sport, model_name, adapter.algorithm,
        adapter.serialize(winner["estimator"]), adapter.artifact_filename,
        metadata, summary_metrics,
    )
    logger.info(
        "%s/%s: %s wins this run's %d-candidate tournament (%s=%.4f) -- only this candidate's artifact is saved (v%d)",
        sport, model_name, adapter.algorithm, len(evaluated), promotion_metric, winner["metrics"][promotion_metric],
        winner_card["version"],
    )
    promoted = training_common.promote_if_better(s3, sport, model_name, winner_card["version"], winner_card, promotion_metric)

    return {"winner": winner_card, "promoted": promoted, "candidates": candidate_summary}
