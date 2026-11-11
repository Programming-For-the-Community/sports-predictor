"""
Season-to-date context for one side of a game, from that team's prior
completed events (most recent first): strength of schedule (mean pre-game
Elo of this season's opponents) and win rates in its own building and on
the road. Neutral-site games count toward neither split.
"""
from library.features.common import average_opponent_elo, games_this_season


def _won(event: dict, team_id: str) -> float | None:
    participants = event.get("participants", [])
    own = next((p for p in participants if p.get("entity_id") == team_id), {})
    opponent = next((p for p in participants if p.get("entity_id") != team_id), {})
    own_score, opp_score = (own.get("result") or {}).get("score"), (opponent.get("result") or {}).get("score")
    if own_score is None or opp_score is None or own_score == opp_score:
        return None
    return float(own_score > opp_score)


def _win_pct(events: list[dict], team_id: str, role: str) -> float | None:
    results = [
        won for e in events
        if not e.get("neutral_site")
        and any(p.get("entity_id") == team_id and p.get("role") == role for p in e.get("participants", []))
        and (won := _won(e, team_id)) is not None
    ]
    return sum(results) / len(results) if results else None


def side_context(team_id: str, team_events: list[dict], as_of: str, elo_ratings: dict) -> dict:
    season = team_events[: games_this_season(team_events, as_of) or 0]
    return {
        "strength_of_schedule": average_opponent_elo(season, team_id, elo_ratings),
        "win_pct_home": _win_pct(season, team_id, "home"),
        "win_pct_road": _win_pct(season, team_id, "away"),
    }


def context_columns(event: dict, home_id: str, away_id: str, home_events: list[dict], away_events: list[dict], elo_ratings: dict) -> dict:
    as_of = event["event_date"]
    home, away = side_context(home_id, home_events, as_of, elo_ratings), side_context(away_id, away_events, as_of, elo_ratings)
    neutral = event.get("neutral_site")
    return {
        "is_neutral_site": None if neutral is None else int(bool(neutral)),
        **{f"home_{k}": v for k, v in home.items()},
        **{f"away_{k}": v for k, v in away.items()},
    }
