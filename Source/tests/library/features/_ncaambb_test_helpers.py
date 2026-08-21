"""
Shared test fixture for test_ncaambb.py's split of library.features.ncaambb
-- mirrors _nba_test_helpers.py's shape and reasoning. No week/season_type/
coach/venue_indoor/travel fields -- NCAA MBB's own feature functions don't
read any of those (see library.features.ncaambb's own docstring).
conference_competition is the one addition over NBA's own helper, since
is_conference_game reads it directly off the event dict.
"""


def event(
    event_key, event_date, home_id, away_id, home_score=None, away_score=None,
    kickoff_time=None, conference_competition=None,
):
    home_result = {"score": home_score, "won": home_score is not None and home_score > away_score}
    away_result = {"score": away_score, "won": away_score is not None and away_score > home_score}
    return {
        "event_key": event_key,
        "event_date": event_date,
        "kickoff_time": kickoff_time,
        "conference_competition": conference_competition,
        "participants": [
            {"entity_id": home_id, "role": "home", "result": home_result},
            {"entity_id": away_id, "role": "away", "result": away_result},
        ],
    }
