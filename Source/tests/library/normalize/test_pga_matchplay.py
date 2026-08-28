"""
Unit tests for library.normalize.pga_matchplay. Every fixture shape below
mirrors a real ESPN golf leaderboard response field-for-field (verified
live 2026-08-26 against a real Presidents Cup, a real WGC-Dell
Technologies Match Play, and two real editions of The Match -- see
project-pga-onboarding memory), not a guessed shape.
"""
import pytest

from library.normalize.pga_matchplay import (
    is_exhibition,
    is_individual_match_play,
    is_supported_match_play,
    is_team_match_play,
    leaderboard_event_to_cup_event_item,
    leaderboard_event_to_match_event_items,
    leaderboard_event_to_matchplay_player_entities,
    leaderboard_event_to_matchplay_team_entities,
)


def _golfer_competitor(home_away, athlete_id, name, won=False, halved=False, margin="", value=0.0, status_name="STATUS_FINISH"):
    return {
        "id": athlete_id,
        "homeAway": home_away,
        "status": {"type": {"id": "2", "name": status_name, "state": "post", "completed": True}},
        "score": {"value": value, "displayValue": margin, "draw": halved, "winner": won},
        "athlete": {"id": athlete_id, "displayName": name, "flag": {"alt": "USA"}, "amateur": False},
    }


def _team_competitor(home_away, team_id, team_display, golfer_ids_names, won=False, halved=False, margin="", value=0.0):
    return {
        "id": golfer_ids_names[0][0],
        "homeAway": home_away,
        "status": {"type": {"id": "2", "name": "STATUS_FINISH", "state": "post", "completed": True}},
        "score": {"value": value, "displayValue": margin, "draw": halved, "winner": won},
        "team": {"id": team_id, "abbreviation": team_display, "displayName": team_display},
        "roster": [
            {"playerId": gid, "athlete": {"id": gid, "displayName": name, "flag": {"alt": "USA"}, "amateur": False}}
            for gid, name in golfer_ids_names
        ],
    }


def _cup_summary_entry(home_id="1", home_display="USA", home_points=17.5, away_id="3", away_display="INTL", away_points=12.5):
    return {
        "id": "10950",
        "description": "tournament",
        "type": {"id": "1", "text": "tournament"},
        "scoringSystem": {"id": "4", "name": "Cup"},
        "competitors": [
            {"id": home_id, "homeAway": "home", "score": {"value": home_points, "displayValue": str(home_points), "winner": home_points > away_points}, "team": {"id": home_id, "abbreviation": home_display, "displayName": home_display}},
            {"id": away_id, "homeAway": "away", "score": {"value": away_points, "displayValue": str(away_points), "winner": away_points > home_points}, "team": {"id": away_id, "abbreviation": away_display, "displayName": away_display}},
        ],
    }


def _team_match_entry(match_id="10951", description="Thursday Foursomes", type_text="foursome", competitors=None):
    return {
        "id": match_id,
        "date": "2022-09-22T17:05Z",
        "description": description,
        "type": {"id": "5", "text": type_text},
        "scoringSystem": {"id": "2", "name": "Match"},
        # No "completed" key here, unlike the top-level tournament status
        # in _cup_event/_wgc_event below -- confirmed live 2026-08-27 on a
        # real cached Presidents Cup (401465497) that an individual
        # match's own status object never carries "completed" at all,
        # only "state"/"name"/"description". See event_status's own
        # docstring (library/normalize/pga.py) for the real crash this
        # caused (every match_play event silently stored as "scheduled").
        "status": {"type": {"id": "3", "name": "STATUS_FINAL", "state": "post"}},
        "competitors": competitors if competitors is not None else [
            _team_competitor("home", "1", "USA", [("1085", "Tony Finau"), ("1086", "Max Homa")], won=True, margin="6 & 5", value=6.0),
            _team_competitor("away", "3", "INTL", [("2001", "Hideki Matsuyama"), ("2002", "Sungjae Im")]),
        ],
    }


def _cup_event(tournament_name="Presidents Cup", sessions=None):
    return {
        "id": "401465497",
        "date": "2022-09-22T17:05Z",
        "endDate": "2022-09-25T04:00Z",
        "season": {"year": 2023},
        "seasonType": {"id": "2", "name": "Regular Season"},
        "tournament": {"id": "40", "displayName": tournament_name, "scoringSystem": {"id": "2", "name": "Match"}},
        "status": {"type": {"id": "3", "name": "STATUS_FINAL", "state": "post", "completed": True}},
        "courses": [{"id": "83", "name": "Quail Hollow Club", "host": True, "address": {"city": "Charlotte", "state": "NC"}}],
        "competitions": sessions if sessions is not None else [
            [_cup_summary_entry()],
            [_team_match_entry()],
        ],
    }


def _wgc_match_entry(match_id="1", description="Rd of 16", competitors=None):
    return {
        "id": match_id,
        "date": "2022-03-27T17:05Z",
        "description": description,
        "type": {"id": "3", "text": "singles"},
        "scoringSystem": {"id": "2", "name": "Match"},
        # No "completed" key -- see _team_match_entry's own comment above.
        "status": {"type": {"id": "3", "name": "STATUS_FINAL", "state": "post"}},
        "competitors": competitors if competitors is not None else [
            _golfer_competitor("home", "3439", "Scottie Scheffler", won=True, margin="3 & 2", value=3.0),
            _golfer_competitor("away", "3448", "Cameron Young"),
        ],
    }


def _wgc_event(sessions=None):
    return {
        "id": "401353293",
        "date": "2022-03-23T07:00Z",
        "season": {"year": 2022},
        "seasonType": {"id": "2", "name": "Regular Season"},
        "tournament": {"id": "473", "displayName": "WGC-Dell Technologies Match Play", "scoringSystem": {"id": "2", "name": "Match"}},
        "status": {"type": {"id": "3", "name": "STATUS_FINAL", "state": "post", "completed": True}},
        "courses": [{"id": "2233", "name": "Austin Country Club", "host": True, "address": {"city": "Austin", "state": "TX"}}],
        "competitions": sessions if sessions is not None else [[_wgc_match_entry()]],
    }


def _the_match_event():
    # No Cup summary entry -- a single one-off foursome, structurally
    # identical to a Ryder Cup foursomes match otherwise (team + roster).
    return {
        "id": "401430881",
        "date": "2022-06-01T22:30Z",
        "season": {"year": 2022},
        "seasonType": {"id": "2", "name": "Regular Season"},
        "tournament": {"id": "3586", "displayName": "The Match", "scoringSystem": {"id": "2", "name": "Match"}},
        "status": {"type": {"id": "3", "name": "STATUS_FINAL", "state": "post", "completed": True}},
        "courses": [{"id": "9999", "name": "Wynn Golf Club", "host": True, "address": {"city": "Las Vegas", "state": "NV"}}],
        "competitions": [[
            _team_match_entry(
                match_id="10823", description="foursome", type_text="foursome",
                competitors=[
                    _team_competitor("home", "1006", "Mahomes/Allen", [("3139477", "Patrick Mahomes"), ("3918298", "Josh Allen")]),
                    _team_competitor("away", "1005", "Brady/Rodgers", [("1870523", "Tom Brady"), ("2129320", "Aaron Rodgers")], won=True, margin="1 Up", value=1.0),
                ],
            ),
        ]],
    }


class TestIsTeamMatchPlay:
    def test_true_for_presidents_cup(self):
        assert is_team_match_play(_cup_event()) is True

    def test_false_for_wgc(self):
        assert is_team_match_play(_wgc_event()) is False

    def test_false_for_the_match(self):
        assert is_team_match_play(_the_match_event()) is False

    def test_false_for_medal_scoring(self):
        assert is_team_match_play({"tournament": {"scoringSystem": {"name": "Medal"}}}) is False


class TestIsIndividualMatchPlay:
    def test_true_for_wgc(self):
        assert is_individual_match_play(_wgc_event()) is True

    def test_false_for_presidents_cup(self):
        assert is_individual_match_play(_cup_event()) is False

    def test_false_for_the_match(self):
        assert is_individual_match_play(_the_match_event()) is False


class TestIsExhibition:
    def test_true_for_the_match(self):
        assert is_exhibition(_the_match_event()) is True

    def test_false_for_presidents_cup(self):
        # Same team+roster shape as The Match -- only the Cup summary
        # entry's presence distinguishes them.
        assert is_exhibition(_cup_event()) is False

    def test_false_for_wgc(self):
        assert is_exhibition(_wgc_event()) is False


class TestIsSupportedMatchPlay:
    def test_true_for_presidents_cup_and_wgc(self):
        assert is_supported_match_play(_cup_event()) is True
        assert is_supported_match_play(_wgc_event()) is True

    def test_false_for_the_match(self):
        assert is_supported_match_play(_the_match_event()) is False


class TestLeaderboardEventToCupEventItem:
    def test_builds_team_level_result(self):
        item = leaderboard_event_to_cup_event_item(_cup_event(), "pga")
        assert item["event_type"] == "cup"
        assert item["event_id"] == "401465497"
        by_id = {p["entity_id"]: p for p in item["participants"]}
        assert by_id["1"]["result"] == {"points": 17.5, "won": True, "halved": False}
        assert by_id["3"]["result"] == {"points": 12.5, "won": False, "halved": False}

    def test_tied_cup_is_flagged_halved(self):
        event = _cup_event(sessions=[
            [_cup_summary_entry(home_points=14.0, away_points=14.0)],
            [_team_match_entry()],
        ])
        item = leaderboard_event_to_cup_event_item(event, "pga")
        assert all(p["result"]["halved"] is True for p in item["participants"])
        assert all(p["result"]["won"] is False for p in item["participants"])

    def test_venue_comes_from_host_course(self):
        item = leaderboard_event_to_cup_event_item(_cup_event(), "pga")
        assert item["venue_name"] == "Quail Hollow Club"
        assert item["venue_city"] == "Charlotte"

    def test_end_date_is_truncated_to_the_date(self):
        item = leaderboard_event_to_cup_event_item(_cup_event(), "pga")
        assert item["end_date"] == "2022-09-25"

    def test_end_date_is_none_when_missing(self):
        event = _cup_event()
        del event["endDate"]
        item = leaderboard_event_to_cup_event_item(event, "pga")
        assert item["end_date"] is None

    def test_returns_none_for_individual_match_play(self):
        assert leaderboard_event_to_cup_event_item(_wgc_event(), "pga") is None

    def test_raises_for_unsupported_event(self):
        with pytest.raises(ValueError):
            leaderboard_event_to_cup_event_item(_the_match_event(), "pga")


class TestLeaderboardEventToMatchEventItems:
    def test_one_row_per_match_excludes_cup_summary(self):
        items = leaderboard_event_to_match_event_items(_cup_event(), "pga")
        assert len(items) == 1
        assert items[0]["event_id"] == "401465497-match-10951"

    def test_match_event_type_and_parent_id(self):
        items = leaderboard_event_to_match_event_items(_cup_event(), "pga")
        assert items[0]["event_type"] == "match_play"
        assert items[0]["parent_event_id"] == "401465497"
        assert items[0]["session_name"] == "Thursday Foursomes"
        assert items[0]["match_format"] == "foursome"

    def test_match_time_is_the_full_untruncated_per_match_timestamp(self):
        items = leaderboard_event_to_match_event_items(_cup_event(), "pga")
        assert items[0]["match_time"] == "2022-09-22T17:05Z"

    def test_two_matches_in_the_same_session_carry_distinct_match_times(self):
        # Confirmed live 2022-09-22: two Thursday Foursomes matches teed
        # off 12 minutes apart -- this is a real, staggered per-match
        # signal, not a tournament-wide placeholder.
        second_match = _team_match_entry(match_id="10956", competitors=[
            _team_competitor("home", "1", "USA", [("1087", "C"), ("1088", "D")]),
            _team_competitor("away", "3", "INTL", [("2003", "E"), ("2004", "F")]),
        ])
        second_match["date"] = "2022-09-22T17:17Z"
        event = _cup_event(sessions=[[_cup_summary_entry()], [_team_match_entry(), second_match]])
        items = leaderboard_event_to_match_event_items(event, "pga")
        match_times = {item["event_id"]: item["match_time"] for item in items}
        assert match_times["401465497-match-10951"] == "2022-09-22T17:05Z"
        assert match_times["401465497-match-10956"] == "2022-09-22T17:17Z"

    def test_team_match_participants_carry_golfer_ids_and_team_entity_id(self):
        items = leaderboard_event_to_match_event_items(_cup_event(), "pga")
        home = next(p for p in items[0]["participants"] if p["role"] == "home")
        assert home["entity_id"] == "1"  # USA team id
        assert home["golfer_entity_ids"] == ["1085", "1086"]

    def test_winning_side_result(self):
        items = leaderboard_event_to_match_event_items(_cup_event(), "pga")
        home = next(p for p in items[0]["participants"] if p["role"] == "home")
        away = next(p for p in items[0]["participants"] if p["role"] == "away")
        assert home["result"] == {"status": "finished", "won": True, "halved": False, "margin_display": "6 & 5", "margin_holes": 6.0}
        assert away["result"]["won"] is False
        assert away["result"]["margin_holes"] is None

    def test_halved_match(self):
        entries = [
            _cup_summary_entry(),
        ]
        halved_match = _team_match_entry(competitors=[
            _team_competitor("home", "1", "USA", [("1", "A")], halved=True, margin="Halved"),
            _team_competitor("away", "3", "INTL", [("2", "B")], halved=True, margin="Halved"),
        ])
        event = _cup_event(sessions=[[entries[0]], [halved_match]])
        items = leaderboard_event_to_match_event_items(event, "pga")
        home = next(p for p in items[0]["participants"] if p["role"] == "home")
        assert home["result"]["won"] is False
        assert home["result"]["halved"] is True

    def test_individual_match_play_participant_uses_golfer_id_directly(self):
        items = leaderboard_event_to_match_event_items(_wgc_event(), "pga")
        home = next(p for p in items[0]["participants"] if p["role"] == "home")
        assert home["entity_id"] == "3439"
        assert home["golfer_entity_ids"] == ["3439"]

    def test_multiple_sessions_all_produce_rows(self):
        event = _cup_event(sessions=[
            [_cup_summary_entry()],
            [_team_match_entry(match_id="10951"), _team_match_entry(match_id="10956")],
            [_team_match_entry(match_id="10975", description="Sunday Singles", type_text="singles")],
        ])
        items = leaderboard_event_to_match_event_items(event, "pga")
        assert len(items) == 3

    def test_raises_for_unsupported_event(self):
        with pytest.raises(ValueError):
            leaderboard_event_to_match_event_items(_the_match_event(), "pga")

    def test_finished_match_status_maps_to_completed_even_with_no_completed_key(self):
        """Real crash, 2026-08-27 (project-pga-onboarding memory): a real
        match's own status object never carries "completed" at all (see
        _team_match_entry's fixture comment) -- every match_play event
        was silently written to DynamoDB as "scheduled" forever, so
        feature-engineering's match/cup dataset build always saw 0 of
        them. event_status now keys off "state" instead, present at this
        nesting level too."""
        items = leaderboard_event_to_match_event_items(_cup_event(), "pga")
        assert items[0]["status"] == "completed"

    def test_individual_match_play_status_also_maps_to_completed(self):
        items = leaderboard_event_to_match_event_items(_wgc_event(), "pga")
        assert items[0]["status"] == "completed"


class TestLeaderboardEventToMatchplayTeamEntities:
    def test_builds_both_national_teams(self):
        entities = leaderboard_event_to_matchplay_team_entities(_cup_event(), "pga")
        assert {e["entity_id"] for e in entities} == {"1", "3"}
        assert all(e["entity_type"] == "team" for e in entities)

    def test_empty_for_individual_match_play(self):
        assert leaderboard_event_to_matchplay_team_entities(_wgc_event(), "pga") == []


class TestLeaderboardEventToMatchplayPlayerEntities:
    def test_builds_golfers_from_team_match_rosters(self):
        entities = leaderboard_event_to_matchplay_player_entities(_cup_event(), "pga")
        ids = {e["entity_id"] for e in entities}
        assert ids == {"1085", "1086", "2001", "2002"}
        assert all(e["entity_type"] == "player" for e in entities)

    def test_builds_golfers_from_individual_match_athletes(self):
        entities = leaderboard_event_to_matchplay_player_entities(_wgc_event(), "pga")
        assert {e["entity_id"] for e in entities} == {"3439", "3448"}

    def test_deduplicates_a_golfer_across_multiple_sessions(self):
        event = _cup_event(sessions=[
            [_cup_summary_entry()],
            [_team_match_entry(match_id="10951")],
            [_team_match_entry(match_id="10975", description="Sunday Singles")],
        ])
        entities = leaderboard_event_to_matchplay_player_entities(event, "pga")
        ids = [e["entity_id"] for e in entities]
        assert len(ids) == len(set(ids))
