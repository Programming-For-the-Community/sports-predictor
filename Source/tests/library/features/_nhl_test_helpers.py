"""
Shared builders for the NHL feature tests: normalized events, team box
rows and player rows in the shapes library.normalize.nhl produces.

Not test_-prefixed, so pytest never collects it as a test module itself.
"""
from library.schema.keys import event_key, player_key, team_key


def event(
    event_id, event_date, home_id, away_id, home_periods, away_periods, *,
    season=2026, season_type=2, shootout_winner=None, venue_city=None, kickoff_time=None,
):
    """A completed event. Periods beyond the third are overtime;
    shootout_winner ("home"/"away") appends the shootout period and
    credits the extra goal the way ESPN's final score does."""
    home_periods, away_periods = list(home_periods), list(away_periods)
    if shootout_winner:
        home_periods.append(1 if shootout_winner == "home" else 0)
        away_periods.append(1 if shootout_winner == "away" else 0)
    home_score, away_score = sum(home_periods), sum(away_periods)
    return {
        "event_key": event_key("nhl", event_id), "event_id": event_id, "sport": "nhl",
        "event_date": event_date, "kickoff_time": kickoff_time or f"{event_date}T23:00Z",
        "status": "completed", "season": season, "season_type": season_type, "venue_city": venue_city,
        "went_to_overtime": len(home_periods) > 3, "decided_by_shootout": bool(shootout_winner),
        "participants": [
            {"entity_id": home_id, "role": "home", "result": {
                "score": home_score, "won": home_score > away_score,
                "period_scores": home_periods, "regulation_score": sum(home_periods[:3]),
            }},
            {"entity_id": away_id, "role": "away", "result": {
                "score": away_score, "won": away_score > home_score,
                "period_scores": away_periods, "regulation_score": sum(away_periods[:3]),
            }},
        ],
    }


def team_box(event_id, team_id, **stats):
    line = {
        "shots_total": 30, "shots_missed": 10, "blocked_shots": 12, "saves": 27, "shots_against": 30,
        "power_play_goals": 1, "power_play_opportunities": 3, "penalty_minutes": 6, "faceoffs_won": 28,
        "takeaways": 5, "giveaways": 8, "hits": 20, **stats,
    }
    return {"event_key": event_key("nhl", event_id), "team_key": team_key(team_id), "team_id": team_id, "stat_line": line}


def skater(event_id, event_date, team_id, player_id, *, toi=900, points=0):
    return {
        "event_key": event_key("nhl", event_id), "player_key": player_key("nhl", player_id), "entity_id": player_id,
        "team_id": team_id, "event_date": event_date, "position_group": "skater",
        "stat_line": {"time_on_ice_seconds": toi, "points": points, "goals": points, "assists": 0},
    }


def goalie(event_id, event_date, team_id, player_id, *, saves=27, shots=30, toi=3600, started=True):
    return {
        "event_key": event_key("nhl", event_id), "player_key": player_key("nhl", player_id), "entity_id": player_id,
        "team_id": team_id, "event_date": event_date, "position_group": "goalie", "started": started,
        "stat_line": {
            "saves": saves, "shots_against": shots, "goals_against": shots - saves, "time_on_ice_seconds": toi,
        },
    }
