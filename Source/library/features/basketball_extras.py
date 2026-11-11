"""
The NBA/NCAA MBB event columns layered on top of common.
build_basketball_event_features: pace, ratings and four factors, season
context, and each side's schedule density, travel and body clock.
"""
from library.features import schedule
from library.features.basketball_box import basketball_box_columns
from library.features.team_context import context_columns


def _side_schedule(event: dict, team_id: str, team_events: list[dict], sites: schedule.SiteTable) -> dict:
    columns = {
        **schedule.density_columns(event, team_id, team_events, sites),
        **schedule.clock_columns(event, team_id, team_events, sites),
    }
    if sites.by_state:
        columns["travel_km"] = schedule.home_distance_km(event, team_id, team_events, sites)
    return columns


def basketball_extra_columns(
    sport: str, event: dict, elo_ratings: dict, home_team_events: list[dict], away_team_events: list[dict],
    home_box: list[dict], away_box: list[dict], window: int, sites: schedule.SiteTable,
) -> dict:
    """Team events and box rows: each side's history, most recent first, not
    including this game (box rows paired, library.features.box_history)."""
    home_id, away_id = (
        next(p["entity_id"] for p in event["participants"] if p.get("role") == role) for role in ("home", "away")
    )
    return {
        **basketball_box_columns(sport, home_box, away_box, window, event["event_date"]),
        **context_columns(event, home_id, away_id, home_team_events, away_team_events, elo_ratings),
        **schedule.side_columns({
            "home": _side_schedule(event, home_id, home_team_events, sites),
            "away": _side_schedule(event, away_id, away_team_events, sites),
        }),
    }
