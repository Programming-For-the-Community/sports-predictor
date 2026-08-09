"""
Shared test fixtures for test_nfl_reads_*.py's own split of what used to
be one large test_nfl_reads.py -- see test_nfl_reads_list_events.py's own
module docstring for why this lives in its own importable (not
test_-prefixed, so pytest never collects it as a test module itself)
file instead of being duplicated across every split file.
"""


def completed_event(event_key, season, home_id, away_id, home_score, away_score, *,
                     event_id=None, event_date="2025-09-14", season_type=2, week=1):
    return {
        "event_key": event_key, "event_id": event_id or event_key, "event_date": event_date,
        "season": season, "season_type": season_type, "week": week, "status": "completed",
        "participants": [
            {"entity_id": home_id, "role": "home", "result": {"score": home_score, "won": home_score > away_score}},
            {"entity_id": away_id, "role": "away", "result": {"score": away_score, "won": away_score > home_score}},
        ],
    }


def scheduled_event(event_key, season, event_date, home_id, away_id, *,
                     event_id=None, season_type=2, week=1):
    return {
        "event_key": event_key, "event_id": event_id or event_key, "event_date": event_date,
        "season": season, "season_type": season_type, "week": week, "status": "scheduled",
        "participants": [
            {"entity_id": home_id, "role": "home", "result": None},
            {"entity_id": away_id, "role": "away", "result": None},
        ],
    }


def prediction_row(model_key, predicted_value):
    return {"model_key": model_key, "predicted_value": predicted_value}
