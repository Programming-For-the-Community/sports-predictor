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

    Writes a full versioned artifact + model card for EVERY candidate
    (nothing is discarded, matching the existing "held-back versions stay
    available for review" philosophy) -- each card also carries a
    `candidates` summary of every algorithm tried this run, ranked
    best-first by promotion_metric (the correct scoring rule for deciding
    a winner) but DISPLAYING each one's accuracy (classification) or mae
    (regression) instead -- human-readable, unlike log_loss/rmse -- so any
    one of them shows what it competed against. Then promotes whichever
    candidate scored best on promotion_metric via the existing,
    algorithm-agnostic training_common.promote_if_better -- the
    runner-up(s) stay versioned and inspectable, just not promoted.

    Returns {"winner": winner_card, "promoted": bool, "candidates": [card, ...]}.
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
    display_metric = "accuracy" if task == "classification" else "mae"
    ranked = sorted(evaluated, key=lambda e: e["metrics"][promotion_metric])
    candidate_summary = [
        {"algorithm": e["adapter"].algorithm, "score": e["metrics"][display_metric]}
        for e in ranked
    ]

    cards = []
    for e in evaluated:
        adapter = e["adapter"]
        metadata = {
            **extra_metadata,
            **e["metrics"],
            **naive_baseline_metrics,
            "feature_importances": e["feature_importances"],
            "hyperparameters": e["hyperparameters"],
            "candidates": candidate_summary,
        }
        card = training_common.save_model_artifact(
            s3, sport, model_name, adapter.algorithm,
            adapter.serialize(e["estimator"]), adapter.artifact_filename,
            metadata, summary_metrics,
        )
        cards.append(card)

    winner = min(cards, key=lambda card: card[promotion_metric])
    logger.info(
        "%s/%s: %s (%s) wins this run's %d-candidate tournament (%s=%.4f)",
        sport, model_name, winner["algorithm"], winner["version"], len(cards), promotion_metric, winner[promotion_metric],
    )
    promoted = training_common.promote_if_better(s3, sport, model_name, winner["version"], winner, promotion_metric)

    return {"winner": winner, "promoted": promoted, "candidates": cards}
