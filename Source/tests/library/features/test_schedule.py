"""Unit tests for library.features.schedule, us_states and team_context."""
import math

from library.features import schedule, team_context, us_states
from library.features.geo import haversine_km

PRO = schedule.SiteTable(
    team_coordinates={"H": (40.0, -74.0), "A": (34.0, -118.0), "C": (41.9, -87.6)},
    team_utc_offsets={"H": -5, "A": -8, "C": -6},
    international_venues={"London": (51.5, -0.1)},
)
COLLEGE = schedule.SiteTable(by_state=True)


def _event(key, date, home="H", away="A", home_score=None, away_score=None, **extra):
    return {
        "event_key": key, "event_date": date, "kickoff_time": f"{date}T17:00Z",
        "participants": [
            {"entity_id": home, "role": "home", "result": {"score": home_score}},
            {"entity_id": away, "role": "away", "result": {"score": away_score}},
        ],
        **extra,
    }


class TestUsStates:
    def test_known_state(self):
        assert us_states.utc_offset("tx") == -6
        assert us_states.coordinates("CA") is not None

    def test_unknown_or_missing_state(self):
        assert us_states.coordinates("ON") is None
        assert us_states.utc_offset(None) is None


class TestSiteTable:
    def test_pro_game_is_at_the_home_market(self):
        assert PRO.site(_event("E", "2025-01-01")) == ((40.0, -74.0), -5)

    def test_pro_international_game_has_no_clock(self):
        assert PRO.site(_event("E", "2025-01-01", venue_city="London")) == ((51.5, -0.1), None)

    def test_college_game_is_at_its_venue_state(self):
        assert COLLEGE.site(_event("E", "2025-01-01", venue_state="AZ")) == (us_states.coordinates("AZ"), -7)

    def test_college_home_is_the_most_common_home_venue_state(self):
        history = [
            _event("E1", "2025-01-01", venue_state="NC"),
            _event("E2", "2025-01-05", venue_state="NC"),
            _event("E3", "2025-01-09", venue_state="NV", neutral_site=True),
            _event("E4", "2025-01-12", home="A", away="H", venue_state="CA"),
        ]
        assert COLLEGE.home("H", history) == (us_states.coordinates("NC"), -5)


class TestClockColumns:
    def test_away_team_two_zones_from_home(self):
        # 17:00 UTC is 9am in Los Angeles; the game is three hours ahead of the visitors' clock.
        columns = schedule.clock_columns(_event("E", "2025-01-01"), "A", [], PRO)
        assert columns == {"timezone_shift_hours": 3, "local_start_hour": 9}

    def test_unknown_kickoff_or_team(self):
        event = _event("E", "2025-01-01", kickoff_time=None)
        assert schedule.clock_columns(event, "Z", [], PRO) == {"timezone_shift_hours": None, "local_start_hour": None}


class TestDensityColumns:
    def test_back_to_back_on_a_road_trip(self):
        history = [
            _event("E2", "2025-01-09", home="C", away="A"),
            _event("E1", "2025-01-07", home="H", away="A"),
        ]
        columns = schedule.density_columns(_event("E3", "2025-01-10"), "A", history, PRO)
        assert columns["is_back_to_back"] == 1
        assert columns["games_last_4_days"] == 2
        assert columns["games_last_7_days"] == 2
        assert columns["road_trip_game_number"] == 3
        assert columns["home_stand_game_number"] == 0
        assert math.isclose(columns["trip_km"], haversine_km((41.9, -87.6), (40.0, -74.0)))

    def test_first_game_of_a_season(self):
        history = [_event("E0", "2024-04-01")]
        columns = schedule.density_columns(_event("E1", "2024-10-20"), "H", history, PRO)
        assert columns["is_back_to_back"] == 0
        assert columns["home_stand_game_number"] == 1
        assert columns["trip_km"] is None

    def test_neutral_site_is_away_for_both_sides(self):
        columns = schedule.density_columns(_event("E1", "2025-03-20", neutral_site=True), "H", [], PRO)
        assert (columns["home_stand_game_number"], columns["road_trip_game_number"]) == (0, 1)
        assert columns["is_back_to_back"] is None


class TestHomeDistance:
    def test_college_distance_from_home_state_to_venue_state(self):
        history = [_event("E1", "2025-01-01", venue_state="NC")]
        event = _event("E2", "2025-01-05", venue_state="NV", neutral_site=True)
        assert math.isclose(
            schedule.home_distance_km(event, "H", history, COLLEGE),
            haversine_km(us_states.coordinates("NC"), us_states.coordinates("NV")),
        )


class TestTeamContext:
    def test_season_splits_and_strength_of_schedule(self):
        history = [
            _event("E3", "2025-01-09", home="H", away="X", home_score=80, away_score=70),
            _event("E2", "2025-01-05", home="Y", away="H", home_score=90, away_score=60),
            _event("E1", "2025-01-01", home="H", away="Z", home_score=60, away_score=70, neutral_site=True),
            _event("E0", "2024-03-01", home="H", away="W", home_score=99, away_score=10),
        ]
        elo_ratings = {"E3": {"away_pre_rating": 1600.0}, "E2": {"home_pre_rating": 1400.0}, "E1": {"away_pre_rating": 1500.0}}
        context = team_context.side_context("H", history, "2025-01-12", elo_ratings)
        assert context == {"strength_of_schedule": 1500.0, "win_pct_home": 1.0, "win_pct_road": 0.0}

    def test_context_columns_flag_a_neutral_site(self):
        columns = team_context.context_columns(_event("E", "2025-01-01", neutral_site=True), "H", "A", [], [], {})
        assert columns["is_neutral_site"] == 1
        assert columns["home_strength_of_schedule"] is None

    def test_neutral_site_unknown(self):
        assert team_context.context_columns(_event("E", "2025-01-01"), "H", "A", [], [], {})["is_neutral_site"] is None
