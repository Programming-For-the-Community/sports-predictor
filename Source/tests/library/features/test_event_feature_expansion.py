"""
The event rows each team sport's build_event_features produces carry the
box-efficiency, context and schedule columns (library.features.
football_box / basketball_box / team_context / schedule).
"""
from library.features import football, nba, ncaafb, ncaambb, nfl, nhl
from library.storage.cfbd_supplements import CfbdSupplements


def _event(key, date, home, away, home_score=None, away_score=None, **extra):
    return {
        "event_key": key, "event_date": date, "season": 2025, "kickoff_time": f"{date}T18:00Z",
        "participants": [
            {"entity_id": home, "role": "home", "result": {"score": home_score, "won": (home_score or 0) > (away_score or 0)}},
            {"entity_id": away, "role": "away", "result": {"score": away_score, "won": (away_score or 0) > (home_score or 0)}},
        ],
        **extra,
    }


def _box(key, date, team, line, opponent_line, points=(20, 10)):
    return {
        "event_key": key, "event_date": date, "team_id": team, "stat_line": line, "opponent_stat_line": opponent_line,
        "points_for": points[0], "points_against": points[1], "periods": None, "advanced": None,
    }


class TestFootballRows:
    def test_nfl_row_has_efficiency_context_clock_and_precipitation(self):
        past = _event("E0", "2025-10-05", "12", "7", 27, 20)
        box = [_box("E0", "2025-10-05", "12", {"total_yards": 400, "total_offensive_plays": 80}, {"total_yards": 300, "total_offensive_plays": 60})]
        event = _event("E1", "2025-10-12", "12", "26", weather_condition_id=18, neutral_site=False)

        row = nfl.build_event_features(event, {"E0": {"away_pre_rating": 1550.0}}, [past], [], home_team_box_stats=box)

        assert row["home_yards_per_play"] == 5.0
        assert row["home_avg_total_yards"] == 400  # the existing base average still reads the same rows
        assert row["is_precipitation"] == 1
        assert row["is_neutral_site"] == 0
        assert row["home_strength_of_schedule"] == 1550.0
        # 18:00 UTC: noon in Kansas City, 10am in Seattle
        assert (row["home_local_start_hour"], row["away_local_start_hour"]) == (12, 10)
        assert row["away_timezone_shift_hours"] == 2

    def test_ncaafb_row_has_advanced_and_season_supplements(self):
        event = _event("E1", "2025-10-12", "61", "333", venue_state="GA")
        supplements = CfbdSupplements(talent={(2025, "61"): 950.0})
        row = ncaafb.build_event_features(event, {}, [], [], {}, supplements=supplements)
        assert row["home_talent"] == 950.0
        assert "home_off_epa_per_play" in row and "home_yards_per_play_allowed" in row
        assert row["home_timezone_shift_hours"] == 0


class TestBasketballRows:
    def test_nba_row_has_ratings_schedule_and_context(self):
        past = _event("E0", "2025-11-09", "2", "18", 110, 100)
        event = _event("E1", "2025-11-10", "2", "13")
        row = nba.build_event_features(event, {}, [past], [])
        assert row["home_is_back_to_back"] == 1
        assert row["away_timezone_shift_hours"] == 3
        assert "home_pace_ewm" in row and "diff_net_rating" in row
        assert "home_offensive_efficiency" in row  # kept until the models retrain on the new columns

    def test_ncaambb_row_has_rank_and_state_based_travel(self):
        event = _event("E1", "2025-11-10", "150", "153", venue_state="NC", home_current_rank=4)
        row = ncaambb.build_event_features(event, {}, [], [])
        assert row["home_current_rank"] == 4 and row["away_current_rank"] is None
        assert "home_travel_km" in row and "home_trip_km" in row


def test_precipitation_flag():
    assert football.is_precipitation(18) == 1
    assert football.is_precipitation(2) == 0
    assert football.is_precipitation(None) is None


def test_nhl_context_flags_a_neutral_site():
    event = _event("E1", "2025-11-10", "1", "2", neutral_site=True)
    assert nhl.context_features(event, "1", "2", [])["is_neutral_site"] == 1
    assert nhl.feature_group("is_neutral_site") == "context"
