"""
Shared test fixture for test_nfl_event_features_*.py and
test_nfl_player_features.py's own split of what used to be one large
test_nfl.py -- see test_nfl_event_features_core.py's own module docstring
for why this lives in its own importable (not test_-prefixed, so pytest
never collects it as a test module itself) file instead of being
duplicated across every split file.
"""


def event(
    event_key, event_date, home_id, away_id, home_score=None, away_score=None, week=1, season_type=2,
    venue_indoor=None, venue_city=None, venue_state=None, weather_temperature=None,
    home_coach_experience=None, away_coach_experience=None,
    home_coach_season_win_pct=None, away_coach_season_win_pct=None,
    home_coach_career_playoff_win_pct=None, away_coach_career_playoff_win_pct=None,
    home_injuries=None, away_injuries=None, kickoff_time=None,
):
    home_result = {"score": home_score, "won": home_score is not None and home_score > away_score}
    away_result = {"score": away_score, "won": away_score is not None and away_score > home_score}
    return {
        "event_key": event_key,
        "event_date": event_date,
        "week": week,
        "season_type": season_type,
        "kickoff_time": kickoff_time,
        "venue_indoor": venue_indoor,
        "venue_city": venue_city,
        "venue_state": venue_state,
        "weather_temperature": weather_temperature,
        "home_coach_experience": home_coach_experience,
        "away_coach_experience": away_coach_experience,
        "home_coach_season_win_pct": home_coach_season_win_pct,
        "away_coach_season_win_pct": away_coach_season_win_pct,
        "home_coach_career_playoff_win_pct": home_coach_career_playoff_win_pct,
        "away_coach_career_playoff_win_pct": away_coach_career_playoff_win_pct,
        "home_injuries": home_injuries,
        "away_injuries": away_injuries,
        "participants": [
            {"entity_id": home_id, "role": "home", "result": home_result},
            {"entity_id": away_id, "role": "away", "result": away_result},
        ],
    }
