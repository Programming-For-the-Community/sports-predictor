"""
A baseline a regressor learns the correction to: a straight line fitted on
training rows and stored on the model card, which predictions add back.
  - {"kind": "elo_linear", "slope", "intercept"}: on home_elo - away_elo.
  - {"kind": "column_linear", "column", "slope", "intercept", "fill"}: on one
    feature column; a row missing it uses `fill`, the training rows' mean.
"""
import numpy as np
import pandas as pd

HOME_ELO = "home_elo"
AWAY_ELO = "away_elo"
MIN_BASELINE_ROWS = 30


def _elo_gap(X: pd.DataFrame) -> pd.Series:
    return X[HOME_ELO] - X[AWAY_ELO]


def fit_elo_linear(X: pd.DataFrame, y: pd.Series) -> dict | None:
    """None without both Elo columns or with fewer than MIN_BASELINE_ROWS rated rows."""
    if HOME_ELO not in X.columns or AWAY_ELO not in X.columns:
        return None
    gap = _elo_gap(X)
    rated = gap.notna() & y.notna()
    if rated.sum() < MIN_BASELINE_ROWS:
        return None
    slope, intercept = np.polyfit(gap[rated].to_numpy(dtype=float), y[rated].to_numpy(dtype=float), 1)
    return {"kind": "elo_linear", "slope": float(slope), "intercept": float(intercept)}


def fit_column_linear(X: pd.DataFrame, y: pd.Series, column: str) -> dict | None:
    """None without `column` or with fewer than MIN_BASELINE_ROWS rows that have it."""
    if column not in X.columns:
        return None
    values = X[column]
    known = values.notna() & y.notna()
    if known.sum() < MIN_BASELINE_ROWS:
        return None
    slope, intercept = np.polyfit(values[known].to_numpy(dtype=float), y[known].to_numpy(dtype=float), 1)
    return {
        "kind": "column_linear", "column": column, "slope": float(slope), "intercept": float(intercept),
        "fill": float(values[known].mean()),
    }


def apply(baseline: dict | None, X: pd.DataFrame) -> np.ndarray:
    """The baseline per row; zeros when `baseline` is None. An unrated elo_linear
    row gets the intercept."""
    if not baseline:
        return np.zeros(len(X))
    if baseline["kind"] == "column_linear":
        values = X[baseline["column"]].astype(float).fillna(baseline["fill"])
        return (baseline["slope"] * values + baseline["intercept"]).to_numpy(dtype=float)
    return (baseline["slope"] * _elo_gap(X).fillna(0.0) + baseline["intercept"]).to_numpy(dtype=float)
