"""
Read-only NBA serving logic -- GET /nba/events, GET /nba/models -- shared
between the heavy inference Lambda (Source/aws-lambdas/nba/predict) and
the light read-only Lambda (Source/aws-lambdas/nba/predict-read).

get_season_projection reads the standings + NBA Cup + player-prop
leaderboard projection written weekly by the scheduled compute path,
never computed live here. No round label -- NBA has no postseason-round
concept.

No round/week grouping -- NBA's schedule is date-based (~10-15
games/night most nights of the season), so list_events groups by
calendar date instead of week. This whole module is a thin re-export of
library.serving.common's basketball-shaped helpers (byte-identical to
library.serving.ncaambb_reads before both were folded into common.py) --
see that module's own docstrings for the actual logic. `_home_and_away`/
`_actual_result`/`SCORE_MODELS`/`WIN_PROBABILITY_MODEL` stay re-exported
here (not just used internally) -- season_projection.py and
event_prediction.py both import them from this module directly.

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
