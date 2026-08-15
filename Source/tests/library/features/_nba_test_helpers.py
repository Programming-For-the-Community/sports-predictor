"""
Shared test fixture for test_nba*.py's own split of library.features.nba
-- mirrors _nfl_test_helpers.py's/_ncaafb_test_helpers.py's shape and
reasoning for why this lives in its own importable (not test_-prefixed)
file. No week/season_type/coach/rank/venue_indoor fields -- NBA's own
feature functions don't read any of those (see library.features.nba's
own docstring -- every NBA arena is indoor, so venue_indoor has no
discriminating value and isn't a feature here, unlike NFL/NCAAFB).
"""


def event(
    event_key, event_date, home_id, away_id, home_score=None, away_score=None,
    venue_city=None, kickoff_time=None,
):
    home_result = {"score": home_score, "won": home_score is not None and home_score > away_score}
    away_result = {"score": away_score, "won": away_score is not None and away_score > home_score}
    return {
        "event_key": event_key,
        "event_date": event_date,
        "kickoff_time": kickoff_time,
        "venue_city": venue_city,
        "participants": [
            {"entity_id": home_id, "role": "home", "result": home_result},
            {"entity_id": away_id, "role": "away", "result": away_result},
        ],
    }
