"""
Read-only NCAA MBB serving logic -- GET /ncaambb/events, GET /ncaambb/models
-- shared between the heavy inference Lambda (Source/aws-lambdas/ncaambb/
predict) and the light read-only Lambda (Source/aws-lambdas/ncaambb/
predict-read). Byte-for-byte the same shape as library.serving.nba_reads
(basketball's leader categories -- scoring/rebounding/assists -- and
day-based event grouping are identical for both sports) -- both are now a
thin re-export of library.serving.common's basketball-shaped helpers; see
that module's own docstrings for the actual logic. `_home_and_away`/
`_actual_result`/`SCORE_MODELS`/`WIN_PROBABILITY_MODEL` stay re-exported
here (not just used internally) -- season_projection.py and
event_prediction.py both import them from this module directly.

get_season_projection reads the standings + bracket projection written by
Terraform/scheduler-ncaambb-season-projection.tf's daily scheduled
compute path (aws-lambdas/ncaambb/predict/season_projection.py, step 8).
Still returns None (mapped to a 503 by predict-read/handler.py) until
that schedule's first real invoke actually writes the S3 object.

Callers own their own storage/s3/predictions_table objects and Lambda-
lifecycle concerns.
"""
from library.serving import common
from library.serving.common import (
    RECENT_EVENTS_LIMIT,
    SCORE_MODELS,
    WIN_PROBABILITY_MODEL,
    get_season_projection,
    list_models,
)

list_events = common.list_events_grouped_by_day
_home_and_away = common._home_and_away
_actual_result = common._actual_result
_prediction_comparison = common._prediction_comparison
_leaders_comparison = common._basketball_leaders_comparison
