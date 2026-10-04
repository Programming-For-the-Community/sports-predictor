"""
An Elo-implied baseline a regressor learns the correction to: a straight line
on home_elo - away_elo, fitted on training rows and stored on the model card
({"kind": "elo_linear", "slope", "intercept"}). Predictions add it back.
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


def apply(baseline: dict | None, X: pd.DataFrame) -> np.ndarray:
    """The baseline per row (an unrated row gets the intercept); zeros when `baseline` is None."""
    if not baseline:
        return np.zeros(len(X))
    return (baseline["slope"] * _elo_gap(X).fillna(0.0) + baseline["intercept"]).to_numpy(dtype=float)
