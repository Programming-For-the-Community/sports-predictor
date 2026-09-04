"""
Unit tests for library.normalize.ncaafb.game_to_event_item -- home/away
id resolution from CFBD's flat homeId/awayId (unlike ESPN's nested
competitions[0].competitors[]), score/won derivation from homePoints/
awayPoints, and the coach/rank/venue_indoor fields attached by ingest's
enrich_games (aws-lambdas/ncaafb/ingest/enrichment.py) before this
function ever sees the game. Hand-built synthetic payloads.
"""
from library.normalize.ncaafb import game_to_event_item


def _game(game_id="401520281", home_id="2", away_id="52", **extra):
    return {
        "id": game_id,
        "season": 2025,
        "seasonType": "regular",
        "week": 4,
        "startDate": "2025-09-28T20:25:00.000Z",
        "completed": False,
        "homeId": home_id,
        "awayId": away_id,
        "homePoints": None,
        "awayPoints": None,
        "homeConference": "SEC",
        "awayConference": "Big Ten",
        "conferenceGame": False,
        **extra,
    }


class TestGameToEventItem:
    def test_event_date_is_the_us_eastern_calendar_date(self):
        item = game_to_event_item(_game(), "ncaafb")
        assert item["event_date"] == "2025-09-28"

    def test_event_date_for_a_late_kickoff_is_not_the_raw_utc_date(self):
        # 00:00 UTC is 8pm Eastern the day before -- event_date must stay
        # on the Eastern calendar date the game is actually played on, not
        # the UTC date a raw [:10] truncation would give ("2025-09-29").
        item = game_to_event_item(_game(startDate="2025-09-29T00:00:00.000Z"), "ncaafb")
        assert item["event_date"] == "2025-09-28"

    def test_kickoff_time_keeps_the_full_timestamp(self):
        item = game_to_event_item(_game(), "ncaafb")
        assert item["kickoff_time"] == "2025-09-28T20:25:00.000Z"

    def test_status_completed_when_cfbd_says_completed(self):
        item = game_to_event_item(_game(completed=True), "ncaafb")
        assert item["status"] == "completed"

    def test_status_scheduled_when_not_completed(self):
        item = game_to_event_item(_game(completed=False), "ncaafb")
        assert item["status"] == "scheduled"

    def test_participants_use_home_id_and_away_id_directly(self):
        item = game_to_event_item(_game(home_id="2", away_id="52"), "ncaafb")
        participants = {p["role"]: p["entity_id"] for p in item["participants"]}
        assert participants == {"home": "2", "away": "52"}

    def test_score_and_won_are_none_before_the_game_is_played(self):
        item = game_to_event_item(_game(homePoints=None, awayPoints=None), "ncaafb")
        for participant in item["participants"]:
            assert participant["result"]["score"] is None
            assert participant["result"]["won"] is None

    def test_home_team_winning_is_reflected_per_participant(self):
        item = game_to_event_item(_game(completed=True, homePoints=31, awayPoints=17), "ncaafb")
        by_role = {p["role"]: p["result"] for p in item["participants"]}
        assert by_role["home"] == {"score": 31, "won": True}
        assert by_role["away"] == {"score": 17, "won": False}

    def test_conference_fields_pass_through_as_is(self):
        item = game_to_event_item(_game(homeConference="SEC", awayConference="Big Ten", conferenceGame=False), "ncaafb")
        assert item["home_conference"] == "SEC"
        assert item["away_conference"] == "Big Ten"
        assert item["conference_game"] is False

    def test_venue_indoor_passed_through_from_enrichment(self):
        item = game_to_event_item(_game(venue_indoor=True), "ncaafb")
        assert item["venue_indoor"] is True

    def test_venue_indoor_none_when_enrichment_never_ran(self):
        item = game_to_event_item(_game(), "ncaafb")
        assert item["venue_indoor"] is None

    def test_venue_city_and_state_passed_through_from_enrichment(self):
        item = game_to_event_item(_game(venue_city="Athens", venue_state="GA"), "ncaafb")
        assert item["venue_city"] == "Athens"
        assert item["venue_state"] == "GA"

    def test_venue_city_and_state_none_when_enrichment_never_ran(self):
        item = game_to_event_item(_game(), "ncaafb")
        assert item["venue_city"] is None
        assert item["venue_state"] is None

    def test_is_playoff_game_true_for_a_real_cfp_game(self):
        item = game_to_event_item(_game(playoff={"competition": "cfp", "round": "first_round"}), "ncaafb")
        assert item["is_playoff_game"] is True

    def test_is_playoff_game_false_for_an_ordinary_bowl(self):
        item = game_to_event_item(_game(seasonType="postseason", notes="Cricket Celebration Bowl"), "ncaafb")
        assert item["is_playoff_game"] is False

    def test_is_playoff_game_false_when_playoff_field_absent(self):
        item = game_to_event_item(_game(), "ncaafb")
        assert item["is_playoff_game"] is False


class TestGameToEventItemCoachAndRank:
    """Coach/rank fields are attached by ingest's enrich_games
    (aws-lambdas/ncaafb/ingest/enrichment.py) before this function ever
    sees the game -- absent entirely when enrichment never ran or a fetch
    failed, same sparse-optional convention NFL's own coach fields use."""

    def test_absent_when_not_present_on_raw_game(self):
        item = game_to_event_item(_game(), "ncaafb")
        for key in (
            "home_coach_name", "home_coach_experience", "home_coach_season_win_pct",
            "away_coach_name", "away_coach_experience", "away_coach_season_win_pct",
            "home_current_rank", "away_current_rank",
        ):
            assert key not in item

    def test_coach_flattened_into_top_level_attributes(self):
        raw = _game(
            home_coach={"coach_name": "Kirby Smart", "coach_experience": 9, "season_win_pct": 0.85},
            away_coach={"coach_name": "Ryan Day", "coach_experience": 7, "season_win_pct": 0.9},
        )

        item = game_to_event_item(raw, "ncaafb")

        assert item["home_coach_name"] == "Kirby Smart"
        assert item["home_coach_experience"] == 9
        assert item["home_coach_season_win_pct"] == 0.85
        assert item["away_coach_name"] == "Ryan Day"

    def test_no_career_playoff_win_pct_field_exists(self):
        # CFBD has no equivalent of ESPN's dedicated career-postseason-
        # record endpoint -- unlike NFL's home_coach_career_playoff_win_pct,
        # there's no field name to even omit here.
        raw = _game(home_coach={"coach_name": "Kirby Smart", "coach_experience": 9, "season_win_pct": 0.85})
        item = game_to_event_item(raw, "ncaafb")
        assert "home_coach_career_playoff_win_pct" not in item

    def test_current_rank_kept_when_present_including_number_one(self):
        # rank=1 must survive an `is not None` check, not a truthiness one
        # that would treat it like a falsy sentinel.
        raw = _game(home_current_rank=1, away_current_rank=None)
        item = game_to_event_item(raw, "ncaafb")
        assert item["home_current_rank"] == 1
        assert "away_current_rank" not in item
