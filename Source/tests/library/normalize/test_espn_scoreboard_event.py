"""
Unit tests for library.normalize.espn.scoreboard_event_to_event_item --
date/kickoff/venue extraction, and the coach/injuries/depth-chart fields
attached by ingest's _enrich_events (aws-lambdas/nfl/ingest/handler.py)
before this function ever sees the event (absent entirely on any event
ingested before that shipped, or where a fetch failed, same sparse-
optional convention weather_temperature already established). Hand-built
synthetic payloads. Split out of what used to be one large test_espn.py
-- see test_espn_player_game_stats.py's own history note.
"""
from library.normalize.espn import scoreboard_event_to_event_item


def _scoreboard_event(event_id="401547417", home_id="12", away_id="24", **extra):
    return {
        "id": event_id,
        "date": "2025-09-28T20:25Z",
        "status": {"type": {"completed": False}},
        "season": {"year": 2025, "type": 2},
        "week": {"number": 4},
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"id": home_id}, "score": "0", "winner": False},
                {"homeAway": "away", "team": {"id": away_id}, "score": "0", "winner": False},
            ],
        }],
        **extra,
    }


class TestScoreboardEventToEventItem:
    def test_event_date_is_truncated_to_the_date(self):
        item = scoreboard_event_to_event_item(_scoreboard_event(), "nfl")
        assert item["event_date"] == "2025-09-28"

    def test_kickoff_time_keeps_the_full_timestamp(self):
        item = scoreboard_event_to_event_item(_scoreboard_event(), "nfl")
        assert item["kickoff_time"] == "2025-09-28T20:25Z"

    def test_venue_name_and_address_are_captured(self):
        raw = _scoreboard_event()
        raw["competitions"][0]["venue"] = {
            "fullName": "Arrowhead Stadium", "indoor": False, "address": {"city": "Kansas City", "state": "MO"},
        }

        item = scoreboard_event_to_event_item(raw, "nfl")

        assert item["venue_name"] == "Arrowhead Stadium"
        assert item["venue_city"] == "Kansas City"
        assert item["venue_state"] == "MO"

    def test_conference_competition_is_passed_through_when_present(self):
        raw = _scoreboard_event()
        raw["competitions"][0]["conferenceCompetition"] = True

        item = scoreboard_event_to_event_item(raw, "ncaambb")

        assert item["conference_competition"] is True

    def test_conference_competition_is_none_when_absent(self):
        # Native-ESPN-field treatment (same as venue_indoor) -- always a
        # present key, None rather than omitted when the source event
        # doesn't carry it at all.
        item = scoreboard_event_to_event_item(_scoreboard_event(), "nfl")

        assert item["conference_competition"] is None

    def test_venue_fields_are_none_when_venue_is_absent(self):
        item = scoreboard_event_to_event_item(_scoreboard_event(), "nfl")
        assert item["venue_name"] is None
        assert item["venue_city"] is None
        assert item["venue_state"] is None


class TestEventStatus:
    def test_completed_when_status_type_completed_is_true(self):
        raw = _scoreboard_event(status={"type": {"completed": True, "name": "STATUS_FINAL"}})
        assert scoreboard_event_to_event_item(raw, "nfl")["status"] == "completed"

    def test_scheduled_for_an_upcoming_game(self):
        item = scoreboard_event_to_event_item(_scoreboard_event(), "nfl")
        assert item["status"] == "scheduled"

    def test_canceled_for_a_real_canceled_game(self):
        # Regression case: 401223873, the canceled 2020 Pro Bowl -- see
        # project-nfl-data-quality-edge-cases. completed is False here,
        # same as a genuinely upcoming game, so without name-checking this
        # used to default to "scheduled" forever.
        raw = _scoreboard_event(status={"type": {"completed": False, "name": "STATUS_CANCELED"}})
        assert scoreboard_event_to_event_item(raw, "nfl")["status"] == "canceled"

    def test_canceled_for_a_postponed_game(self):
        # Regression case: 400951581, hurricane-postponed and replayed
        # under a different event_id -- see project-nfl-data-quality-
        # edge-cases. This event_id itself never gets played.
        raw = _scoreboard_event(status={"type": {"completed": False, "name": "STATUS_POSTPONED"}})
        assert scoreboard_event_to_event_item(raw, "nfl")["status"] == "canceled"

    def test_canceled_for_a_suspended_game(self):
        # Regression case: 401437947, the Damar Hamlin game -- suspended
        # mid-game, later ruled permanently over. See project-nfl-data-
        # quality-edge-cases.
        raw = _scoreboard_event(status={"type": {"completed": False, "name": "STATUS_SUSPENDED"}})
        assert scoreboard_event_to_event_item(raw, "nfl")["status"] == "canceled"


class TestScoreboardEventToEventItemCoachInjuryDepthChart:
    """Coach/injuries/depth-chart are attached by ingest's _enrich_events
    (aws-lambdas/nfl/ingest/handler.py) before this function ever sees
    the event -- these fields are absent entirely on any event ingested
    before that shipped, or where a fetch failed, same sparse-optional
    convention weather_temperature already established."""

    def test_absent_when_not_present_on_raw_event(self):
        item = scoreboard_event_to_event_item(_scoreboard_event(), "nfl")

        for key in (
            "home_coach_id", "home_coach_name", "home_coach_experience", "home_coach_season_win_pct",
            "home_coach_career_playoff_win_pct",
            "away_coach_id", "away_coach_name", "away_coach_experience", "away_coach_season_win_pct",
            "away_coach_career_playoff_win_pct",
            "home_injuries", "away_injuries", "home_depth_chart", "away_depth_chart",
        ):
            assert key not in item

    def test_coach_flattened_into_top_level_attributes(self):
        raw = _scoreboard_event(
            home_coach={
                "coach_id": "1", "coach_name": "Andy Reid", "experience": 27,
                "season_win_pct": 0.7, "career_playoff_win_pct": 0.65,
            },
            away_coach={
                "coach_id": "2", "coach_name": "Jim Harbaugh", "experience": 1,
                "season_win_pct": 0.4, "career_playoff_win_pct": None,
            },
        )

        item = scoreboard_event_to_event_item(raw, "nfl")

        assert item["home_coach_id"] == "1"
        assert item["home_coach_name"] == "Andy Reid"
        assert item["home_coach_experience"] == 27
        assert item["home_coach_season_win_pct"] == 0.7
        assert item["home_coach_career_playoff_win_pct"] == 0.65
        assert item["away_coach_id"] == "2"
        assert item["away_coach_name"] == "Jim Harbaugh"
        assert item["away_coach_career_playoff_win_pct"] is None

    def test_empty_injuries_list_is_kept_not_treated_as_absent(self):
        # An empty list is real signal ("checked, nobody's hurt"), not
        # the same as "never checked" -- must survive as [], not be
        # dropped the way a None value would be.
        raw = _scoreboard_event(home_injuries=[], away_injuries=[{"entity_id": "99", "status": "Out"}])

        item = scoreboard_event_to_event_item(raw, "nfl")

        assert item["home_injuries"] == []
        assert item["away_injuries"] == [{"entity_id": "99", "status": "Out"}]

    def test_depth_chart_passed_through_as_is(self):
        depth_chart = {"qb": {"position": {"abbreviation": "QB"}, "athletes": [{"id": "1"}]}}
        raw = _scoreboard_event(home_depth_chart=depth_chart)

        item = scoreboard_event_to_event_item(raw, "nfl")

        assert item["home_depth_chart"] == depth_chart
        assert "away_depth_chart" not in item

    def test_tournament_note_headline_is_captured(self):
        # notes is a sibling of competitions, not nested inside it --
        # confirmed live against a real NBA Cup group-play game.
        raw = _scoreboard_event(notes=[{"type": "event", "headline": "NBA Cup - Group Play"}])

        item = scoreboard_event_to_event_item(raw, "nba")

        assert item["tournament_note"] == "NBA Cup - Group Play"

    def test_tournament_note_is_absent_when_no_notes_present(self):
        item = scoreboard_event_to_event_item(_scoreboard_event(), "nfl")

        assert "tournament_note" not in item

    def test_tournament_note_is_absent_when_notes_is_an_empty_list(self):
        item = scoreboard_event_to_event_item(_scoreboard_event(notes=[]), "nba")

        assert "tournament_note" not in item
