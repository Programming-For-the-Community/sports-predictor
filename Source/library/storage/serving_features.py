"""
S3 key for the model inputs behind one event's pre-event snapshot, in the
model-artifacts bucket under its own top-level prefix.
"""

SERVING_FEATURES_PREFIX = "serving-features/"


def serving_features_key(sport: str, event_key: str) -> str:
    return f"{SERVING_FEATURES_PREFIX}{sport}/{event_key.rsplit('#', 1)[-1]}.json"
