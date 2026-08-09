"""
Shared test fixture for test_ncaafb_*.py's own split of library.features.
ncaafb -- mirrors _nfl_test_helpers.py's shape and reasoning for why this
lives in its own importable (not test_-prefixed) file.
"""


def event(
    event_key, event_date, home_id, away_id, home_score=None, away_score=None, week=1, season_type="regular",
    venue_indoor=None, kickoff_time=None, conference_game=None, is_playoff_game=False,
    home_conference=None, away_conference=None,
    home_coach_experience=None, away_coach_experience=None,
    home_coach_season_win_pct=None, away_coach_season_win_pct=None,
    home_current_rank=None, away_current_rank=None,
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
        "conference_game": conference_game,
        "is_playoff_game": is_playoff_game,
        "home_conference": home_conference,
        "away_conference": away_conference,
        "home_coach_experience": home_coach_experience,
        "away_coach_experience": away_coach_experience,
        "home_coach_season_win_pct": home_coach_season_win_pct,
        "away_coach_season_win_pct": away_coach_season_win_pct,
        "home_current_rank": home_current_rank,
        "away_current_rank": away_current_rank,
        "participants": [
            {"entity_id": home_id, "role": "home", "result": home_result},
            {"entity_id": away_id, "role": "away", "result": away_result},
        ],
    }
