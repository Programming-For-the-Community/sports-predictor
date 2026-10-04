"""
Platt scaling for a classifier's probabilities: two numbers on the model card
({"method": "platt", "slope", "intercept"} on the log-odds), applied the same
way to holdout predictions, the champion re-score and live serving.
"""
import numpy as np
from sklearn.linear_model import LogisticRegression

_EPSILON = 1e-6
MIN_CALIBRATION_ROWS = 50


def _logit(probabilities) -> np.ndarray:
    clipped = np.clip(np.asarray(probabilities, dtype=float), _EPSILON, 1 - _EPSILON)
    return np.log(clipped / (1 - clipped))


def fit_platt(probabilities, outcomes) -> dict | None:
    """Fitted on probabilities the model made for rows it didn't train on; None
    with fewer than MIN_CALIBRATION_ROWS rows or a single outcome."""
    y = np.asarray(outcomes).astype(int)
    if len(y) < MIN_CALIBRATION_ROWS or len(np.unique(y)) < 2:
        return None
    model = LogisticRegression(C=1e6).fit(_logit(probabilities).reshape(-1, 1), y)
    return {"method": "platt", "slope": float(model.coef_[0][0]), "intercept": float(model.intercept_[0])}


def apply(calibration: dict | None, probabilities) -> np.ndarray:
    """`probabilities` unchanged when `calibration` is None."""
    probabilities = np.asarray(probabilities, dtype=float)
    if not calibration:
        return probabilities
    return 1 / (1 + np.exp(-(calibration["slope"] * _logit(probabilities) + calibration["intercept"])))
