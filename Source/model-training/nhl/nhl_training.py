"""
What the NHL game-model training scripts share: which event_features
columns are never model inputs.

EXCLUDED_FEATURE_GROUPS names the library.features.nhl.FEATURE_GROUPS the
models are not trained on. The dataset still carries every group; a
group is brought back by removing it here.

lineup is excluded because serving cannot rebuild it: training reads who
dressed off the box score, which does not exist before a game
(library.features.hockey_live).
"""
from library.features import nhl

EVENT_FEATURES_KEY = "nhl/training-data/event_features.parquet"

EXCLUDED_FEATURE_GROUPS: frozenset[str] = frozenset({"lineup"})

# `season` orders the rows; it is not a property of a game.
EXTRA_NON_FEATURE_COLUMNS = frozenset({"season"}) | nhl.event_feature_columns(EXCLUDED_FEATURE_GROUPS)
