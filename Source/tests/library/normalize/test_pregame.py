"""Unit tests for library.normalize.pregame."""
from library.normalize.pregame import PREGAME_FIELDS, carry_pregame_fields, is_completed


class TestIsCompleted:
    def test_reads_the_scoreboard_completed_flag(self):
        assert is_completed({"status": {"type": {"completed": True}}}) is True
        assert is_completed({"status": {"type": {"completed": False}}}) is False

    def test_an_event_without_a_status_is_not_completed(self):
        assert is_completed({}) is False


class TestCarryPregameFields:
    def test_fills_missing_or_null_fields_from_the_existing_event(self):
        item = {"event_key": "E", "weather_temperature": None}
        existing = {"weather_temperature": 50, "home_injuries": [], "away_coach_experience": 3}

        assert carry_pregame_fields(item, existing) == {
            "event_key": "E", "weather_temperature": 50, "home_injuries": [], "away_coach_experience": 3,
        }

    def test_values_on_the_new_item_win(self):
        assert carry_pregame_fields({"weather_temperature": 30}, {"weather_temperature": 50}) == {"weather_temperature": 30}

    def test_ignores_fields_that_are_not_pregame(self):
        assert carry_pregame_fields({"event_key": "E"}, {"status": "scheduled"}) == {"event_key": "E"}

    def test_without_an_existing_event_returns_the_item(self):
        item = {"event_key": "E"}
        assert carry_pregame_fields(item, None) is item

    def test_covers_both_sides_coach_fields(self):
        assert {"home_coach_season_win_pct", "away_coach_career_playoff_win_pct"} <= set(PREGAME_FIELDS)
