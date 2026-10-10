"""
Read-only NHL serving logic -- GET /nhl/events, GET /nhl/models -- used
by the shared predict-read Lambda and re-exported for the NHL predict
Lambda.

The schedule is date-based, so list_events groups by calendar date the
way NBA's does (library.serving.common.list_events_grouped_by_day). Each
entry also carries the hockey fields:
- went_to_overtime / decided_by_shootout (null until the game is final);
  each participant's result already carries period_scores and
  regulation_score.
- goalies: each side's probable starting goalie as ESPN lists it,
  {"home": {"entity_id", "name", "status"}, "away": {...}} with status
  "Confirmed" or "Expected", a side omitted when none is listed.

Leader categories, each a list, limited to the stats the app shows:
scoring (goals, assists), shooting (shots_total), physical (hits) and
goaltending (saves -- the starting goalie only).
"""
from library.serving import common
from library.serving.common import (
    RECENT_EVENTS_LIMIT,
    SCORE_MODELS,
    WIN_PROBABILITY_MODEL,
    get_season_projection,
    list_models,
)

SPORT = "nhl"

LEADER_CATEGORY_STATS = {
    "scoring": ["goals", "assists"],
    "shooting": ["shots_total"],
    "physical": ["hits"],
    "goaltending": ["saves"],
}
GOALIE_CATEGORY = "goaltending"
LEADER_CATEGORY_LIMITS = {"scoring": 5, "shooting": 5, "physical": 3, "goaltending": 1}
CATEGORY_PRIMARY_STAT = {category: stats[0] for category, stats in LEADER_CATEGORY_STATS.items()}
_STAT_CATEGORY = {stat: category for category, stats in LEADER_CATEGORY_STATS.items() for stat in stats}

_home_and_away = common._home_and_away
_actual_result = common._actual_result
_prediction_comparison = common._prediction_comparison


def _leaders_comparison(storage, rows: list[dict], sport: str, event: dict) -> dict | None:
    return common.list_leaders_comparison(
        storage, rows, sport, event, _STAT_CATEGORY, LEADER_CATEGORY_LIMITS, CATEGORY_PRIMARY_STAT,
    )


def probable_goalies(storage, sport: str, event: dict) -> dict:
    """{"home": {"entity_id", "name", "status"}, ...} from the event's
    stored probable goalies; a side with none listed is omitted."""
    goalies = {}
    for role in ("home", "away"):
        goalie_id = event.get(f"{role}_probable_goalie_id")
        if not goalie_id:
            continue
        entity = storage.get_entity(sport, goalie_id, "player")
        goalies[role] = {
            "entity_id": goalie_id,
            "name": (entity or {}).get("name"),
            "status": event.get(f"{role}_probable_goalie_status"),
        }
    return goalies


def list_events(storage, predictions_table, sport: str, status: str, model_bucket=None) -> dict:
    def hockey_fields(event: dict) -> dict:
        return {
            "went_to_overtime": event.get("went_to_overtime"),
            "decided_by_shootout": event.get("decided_by_shootout"),
            "goalies": probable_goalies(storage, sport, event),
        }

    return common.list_events_grouped_by_day(
        storage, predictions_table, sport, status, model_bucket=model_bucket,
        leaders_comparison_fn=_leaders_comparison, extra_entry_fn=hockey_fields,
    )
