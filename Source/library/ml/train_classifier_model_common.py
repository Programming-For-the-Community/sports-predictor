"""
Shared binary-classification training logic (F1's podium/winprob/dnf/
sprint-podium/sprint-winprob/constructor-winprob and PGA's top10/top5/
match-winprob/cup-winprob -- confirmed identical shape across all 10
scripts before sharing here, differing only in SPORT/MODEL_NAME/
LABEL_COLUMN/NON_FEATURE_COLUMNS/CANDIDATES and whether the label needs a
pre-split notna filter + int coercion).

Each script keeps its own config and calls into `train()` below, which only
reaches `backtest`/`training_common` as MODULE references -- so patching
`train_X_model.backtest.run_backtest` in a test patches the one shared
`library.ml.backtest` module every caller reads. WinProbabilityJob is the
whole script for a head-to-head sport's win-probability model.
"""
from library.aws.s3_manager import S3Manager
from library.ml import backtest, model_types, training_common

SUMMARY_METRICS = ["accuracy", "log_loss", "naive_baseline_accuracy"]
PROMOTION_METRIC = "log_loss"

WIN_PROBABILITY_MODEL_NAME = "win-probability"
HOME_WON_LABEL = "label_home_won"


def train(
    s3: S3Manager, df, sport: str, model_name: str, *, label_column: str,
    non_feature_columns: set[str], candidates: list, logger,
    drop_null_label: bool = False, coerce_int_label: bool = False,
    naive_baseline_fn=None,
) -> dict:
    """Runs the full candidate tournament and returns run_backtest's
    result ({"promotions": [card, ...], "candidates": [summary, ...]}).
    naive_baseline_fn(y_test) -> float overrides the default majority-class
    baseline -- e.g. a head-to-head sport's win-probability model uses
    "always predict the home team wins" (y_test.mean()) instead, since
    home/away is a real asymmetry these targets don't have."""
    if drop_null_label:
        df = df[df[label_column].notna()]

    feature_columns = training_common.feature_columns(df, non_feature_columns)
    train_df, test_df = training_common.chronological_split(df, training_common.TEST_FRACTION)
    train_date_range = [str(train_df["event_date"].min()), str(train_df["event_date"].max())]
    test_date_range = [str(test_df["event_date"].min()), str(test_df["event_date"].max())]
    logger.info(
        "Training on %d rows (%s to %s), evaluating on %d rows (%s to %s)",
        len(train_df), *train_date_range, len(test_df), *test_date_range,
    )

    X_train = training_common.numeric_frame(train_df, feature_columns)
    y_train = train_df[label_column]
    X_test = training_common.numeric_frame(test_df, feature_columns)
    y_test = test_df[label_column]
    if coerce_int_label:
        y_train = y_train.astype(int)
        y_test = y_test.astype(int)

    if naive_baseline_fn is not None:
        naive_baseline_accuracy = naive_baseline_fn(y_test)
    else:
        naive_baseline_accuracy = float(max(y_test.mean(), 1 - y_test.mean()))
    naive_baseline_metrics = {"naive_baseline_accuracy": naive_baseline_accuracy}

    return backtest.run_backtest(
        s3, sport, model_name, task="classification",
        split=backtest.HoldoutSplit(X_train, y_train, X_test, y_test),
        candidates=candidates,
        naive_baseline_metrics=naive_baseline_metrics,
        extra_metadata={
            "train_rows": int(len(train_df)),
            "test_rows": int(len(test_df)),
            "train_date_range": train_date_range,
            "test_date_range": test_date_range,
        },
        summary_metrics=SUMMARY_METRICS,
        promotion_metric=PROMOTION_METRIC,
        run_id=training_common.resolve_run_id(),
        options=backtest.RunOptions(sample_weights=training_common.recency_weights(train_df["event_date"])),
    )


def _home_win_rate(y_test) -> float:
    """The naive baseline for a home-win label: always predict the holdout's own home-win rate."""
    return float(y_test.mean())


class WinProbabilityJob:
    """A head-to-head sport's whole win-probability training script. `namespace`
    is the script module's globals() (see training_common.TrainingScript)."""

    def __init__(
        self, namespace: dict, sport: str, features_key: str, *,
        extra_non_feature_columns: frozenset[str] = frozenset(), include_lightgbm: bool = True,
    ) -> None:
        self.non_feature_columns = training_common.EVENT_IDENTIFIER_COLUMNS | extra_non_feature_columns
        self.label_column = HOME_WON_LABEL
        self.candidates = model_types.classifier_candidates(include_lightgbm=include_lightgbm)
        self._namespace = namespace
        self._sport = sport
        self._script = training_common.TrainingScript(
            namespace, features_key=features_key, row_noun="event", model_name=WIN_PROBABILITY_MODEL_NAME,
        )

    def feature_columns(self, df) -> list[str]:
        return training_common.feature_columns(df, self.non_feature_columns)

    def train(self, s3: S3Manager, df) -> dict:
        return train(
            s3, df, self._sport, WIN_PROBABILITY_MODEL_NAME,
            label_column=self.label_column, non_feature_columns=self.non_feature_columns,
            candidates=self.candidates, logger=self._namespace["logger"], naive_baseline_fn=_home_win_rate,
        )

    def main(self) -> None:
        self._script.main()
