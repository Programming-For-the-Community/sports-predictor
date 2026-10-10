"""
library.normalize.nhl: the hockey event fields (overtime/shootout, period
scores, probable goalies), the skater/goalie box score, and the
preseason/exhibition filter. Payloads are hand-built, shaped
field-for-field like real ESPN hockey/nhl responses captured 2026-10-09.
"""
from library.normalize import nhl

SKATER_KEYS = [
    "blockedShots", "hits", "takeaways", "plusMinus", "timeOnIce", "powerPlayTimeOnIce", "shortHandedTimeOnIce",
    "evenStrengthTimeOnIce", "shifts", "goals", "ytdGoals", "assists", "shotsTotal", "shotsMissed", "shootoutGoals",
    "faceoffsWon", "faceoffsLost", "faceoffPercent", "giveaways", "penalties", "penaltyMinutes",
]
GOALIE_KEYS = [
    "goalsAgainst", "shotsAgainst", "shootoutSaves", "shootoutShotsAgainst", "saves", "savePct", "evenStrengthSaves",
    "powerPlaySaves", "shortHandedSaves", "timeOnIce", "ytdGoals", "penaltyMinutes",
]


def _competitor(role, team_id, score, periods=None, winner=False, probable=None):
    competitor = {"homeAway": role, "team": {"id": team_id}, "score": score, "winner": winner}
    if periods is not None:
        competitor["linescores"] = [
            {"value": float(value), "displayValue": str(value), "period": number}
            for number, value in enumerate(periods, start=1)
        ]
    if probable is not None:
        goalie_id, status = probable
        competitor["probables"] = [{
            "name": "probableStartingGoalie", "playerId": int(goalie_id),
            "athlete": {"id": goalie_id, "displayName": "Goalie"},
            "status": {"id": "102", "name": status, "type": status.lower()},
        }]
    return competitor


def _event(
    home, away, *, completed=True, detail="Final", status_name="STATUS_FINAL", season_type=2,
    competition_type="STD", event_id="401803584",
):
    return {
        "id": event_id,
        "date": "2026-04-07T23:00Z",
        "season": {"year": 2026, "type": season_type},
        "status": {"type": {"name": status_name, "completed": completed, "detail": detail}},
        "competitions": [{
            "type": {"id": "1", "abbreviation": competition_type},
            "competitors": [home, away],
            "venue": {"fullName": "Amerant Bank Arena", "indoor": True, "address": {"city": "Sunrise", "state": "FL"}},
        }],
    }


def _skater(athlete_id, *, goals="0", assists="0", toi="15:30", shots_missed="1", scratched=False, plus_minus="0"):
    stats = [
        "1", "2", "0", plus_minus, toi, "2:10", "0:45", "12:35", "21", goals, "12", assists, "3", shots_missed, "0",
        "4", "5", "44.4", "1", "0", "0",
    ]
    return {
        "athlete": {
            "id": athlete_id, "displayName": f"Skater {athlete_id}", "jersey": "9", "scratched": scratched,
            "position": {"abbreviation": "C"},
        },
        "stats": stats,
    }


def _goalie(athlete_id, *, saves="29", shots_against="32", toi="65:00", save_pct=".906"):
    return {
        "athlete": {
            "id": athlete_id, "displayName": f"Goalie {athlete_id}", "jersey": "40", "scratched": False,
            "position": {"abbreviation": "G"},
        },
        "stats": ["3", shots_against, "0", "0", saves, save_pct, "20", "9", "0", toi, "0", "0"],
    }


def _team_players(team_id, forwards, defenses, goalies):
    return {
        "team": {"id": team_id},
        "statistics": [
            {"name": "forwards", "keys": SKATER_KEYS, "athletes": forwards},
            {"name": "defenses", "keys": SKATER_KEYS, "athletes": defenses},
            {"name": "skaters", "keys": SKATER_KEYS, "athletes": []},
            {"name": "goalies", "keys": GOALIE_KEYS, "athletes": goalies},
        ],
    }


def _team_block(team_id, **overrides):
    stats = {
        "blockedShots": "14", "hits": "22", "takeaways": "5", "shotsTotal": "32", "powerPlayGoals": "1",
        "powerPlayOpportunities": "3", "powerPlayPct": "33.3", "shortHandedGoals": "0", "shootoutGoals": "1",
        "faceoffsWon": "30", "faceoffPercent": "52.6", "giveaways": "9", "penalties": "4", "penaltyMinutes": "8",
        **overrides,
    }
    return {"team": {"id": team_id}, "statistics": [{"name": name, "displayValue": value} for name, value in stats.items()]}


def _summary(players, teams=None, team_ids=("26", "16")):
    return {
        "header": {
            "id": "401803584",
            "competitions": [{
                "date": "2026-04-07T23:00Z",
                "competitors": [{"team": {"id": team_ids[0]}}, {"team": {"id": team_ids[1]}}],
            }],
        },
        "boxscore": {"players": players, "teams": teams or []},
    }


class TestIsIngestableEvent:
    def test_regular_season_and_playoff_franchise_games_are_ingestable(self):
        home, away = _competitor("home", "26", "4"), _competitor("away", "16", "3")

        assert nhl.is_ingestable_event(_event(home, away, season_type=2))
        assert nhl.is_ingestable_event(_event(home, away, season_type=3, competition_type="QTR"))

    def test_preseason_is_not_ingestable(self):
        assert not nhl.is_ingestable_event(_event(_competitor("home", "26", "4"), _competitor("away", "16", "3"), season_type=1))

    def test_all_star_and_international_teams_are_not_ingestable(self):
        # ESPN lists both under the regular season, with non-franchise team ids.
        all_star = _event(_competitor("home", "129030", "4"), _competitor("away", "129031", "3"), competition_type="ALLSTAR")
        four_nations = _event(_competitor("home", "47836", "3"), _competitor("away", "47844", "1"), competition_type="QRR")

        assert not nhl.is_ingestable_event(all_star)
        assert not nhl.is_ingestable_event(four_nations)

    def test_event_with_no_competitors_is_not_ingestable(self):
        assert not nhl.is_ingestable_event({"season": {"type": 2}, "competitions": [{"competitors": []}]})
        assert not nhl.is_ingestable_event({"season": {"type": 2}})

    def test_relocated_franchises_old_id_is_still_a_franchise(self):
        assert nhl.is_ingestable_event(_event(_competitor("home", "24", "2"), _competitor("away", "8", "1")))


class TestIsIngestableSummary:
    def test_franchise_box_score_is_ingestable_and_an_all_star_one_is_not(self):
        assert nhl.is_ingestable_summary(_summary([]))
        assert not nhl.is_ingestable_summary(_summary([], team_ids=("129030", "129031")))
        assert not nhl.is_ingestable_summary({})


class TestScoreboardEventToEventItem:
    def test_regulation_game(self):
        event = _event(
            _competitor("home", "13", "3", [1, 2, 0]), _competitor("away", "2", "5", [2, 0, 3], winner=True),
        )

        item = nhl.scoreboard_event_to_event_item(event, "nhl")

        assert item["went_to_overtime"] is False
        assert item["decided_by_shootout"] is False
        home, away = item["participants"]
        assert home["result"] == {"score": 3, "won": False, "period_scores": [1, 2, 0], "regulation_score": 3}
        assert away["result"]["regulation_score"] == 5

    def test_overtime_game_keeps_the_overtime_goal_out_of_the_regulation_score(self):
        event = _event(
            _competitor("home", "1", "6", [2, 3, 0, 1], winner=True), _competitor("away", "7", "5", [3, 1, 1, 0]),
            detail="Final/OT",
        )

        item = nhl.scoreboard_event_to_event_item(event, "nhl")

        assert item["went_to_overtime"] is True
        assert item["decided_by_shootout"] is False
        assert [p["result"]["regulation_score"] for p in item["participants"]] == [5, 5]
        assert item["participants"][0]["result"]["score"] == 6

    def test_shootout_game_is_flagged_and_final_score_keeps_espns_extra_goal(self):
        event = _event(
            _competitor("home", "26", "4", [0, 1, 2, 0, 1], winner=True), _competitor("away", "16", "3", [1, 1, 1, 0, 0]),
            detail="Final/SO",
        )

        item = nhl.scoreboard_event_to_event_item(event, "nhl")

        assert item["went_to_overtime"] is True
        assert item["decided_by_shootout"] is True
        home, away = item["participants"]
        assert (home["result"]["score"], home["result"]["regulation_score"]) == (4, 3)
        assert (away["result"]["score"], away["result"]["regulation_score"]) == (3, 3)

    def test_multiple_overtime_playoff_game_is_not_a_shootout(self):
        event = _event(
            _competitor("home", "26", "3", [1, 1, 0, 0, 1], winner=True), _competitor("away", "16", "2", [0, 2, 0, 0, 0]),
            detail="Final/2OT", season_type=3,
        )

        item = nhl.scoreboard_event_to_event_item(event, "nhl")

        assert item["went_to_overtime"] is True
        assert item["decided_by_shootout"] is False

    def test_scheduled_game_carries_probable_goalies_but_no_period_fields(self):
        event = _event(
            _competitor("home", "1", "0", probable=("4712036", "Expected")),
            _competitor("away", "7", "0", probable=("4996097", "Confirmed")),
            completed=False, status_name="STATUS_SCHEDULED", detail="Sat, October 10th at 1:00 PM EDT",
        )

        item = nhl.scoreboard_event_to_event_item(event, "nhl")

        assert item["status"] == "scheduled"
        assert (item["home_probable_goalie_id"], item["home_probable_goalie_status"]) == ("4712036", "Expected")
        assert (item["away_probable_goalie_id"], item["away_probable_goalie_status"]) == ("4996097", "Confirmed")
        assert "went_to_overtime" not in item
        assert "period_scores" not in item["participants"][0]["result"]

    def test_no_probables_leaves_the_goalie_fields_off(self):
        # ESPN has none on scoreboards before the 2021 season.
        item = nhl.scoreboard_event_to_event_item(
            _event(_competitor("home", "27", "4", [0, 2, 2]), _competitor("away", "12", "3", [1, 1, 1])), "nhl",
        )

        assert "home_probable_goalie_id" not in item
        assert "away_probable_goalie_status" not in item

    def test_completed_game_without_linescores_leaves_the_overtime_flags_off(self):
        item = nhl.scoreboard_event_to_event_item(_event(_competitor("home", "27", "4"), _competitor("away", "12", "3")), "nhl")

        assert "went_to_overtime" not in item

    def test_carries_the_shared_event_fields_and_competition_type(self):
        item = nhl.scoreboard_event_to_event_item(
            _event(_competitor("home", "26", "4", [1, 1, 2]), _competitor("away", "16", "3", [1, 1, 1])), "nhl",
        )

        assert item["event_key"] == "SPORT#NHL#EVENT#401803584"
        assert item["event_date"] == "2026-04-07"
        assert item["event_type"] == "head_to_head"
        assert item["competition_type"] == "STD"
        assert item["venue_city"] == "Sunrise"


class TestBoxscoreToPlayerGameStats:
    def test_skater_line_has_no_category_prefix_and_parses_ice_time_to_seconds(self):
        summary = _summary([_team_players("26", [_skater("101", goals="1", assists="2", plus_minus="+1")], [], [])])

        [item], _ = nhl.boxscore_to_player_game_stats(summary, "nhl")

        line = item["stat_line"]
        assert (item["position_group"], item["position"]) == ("skater", "C")
        assert "started" not in item
        assert (line["goals"], line["assists"], line["points"]) == (1, 2, 3)
        assert line["plus_minus"] == 1
        assert line["time_on_ice_seconds"] == 15 * 60 + 30
        assert line["power_play_time_on_ice_seconds"] == 130
        assert line["shots_total"] == 3
        assert line["faceoff_percent"] == 44.4
        assert "time_on_ice" not in line
        assert not any(key.startswith("forwards_") for key in line)

    def test_forwards_and_defensemen_share_the_same_field_names(self):
        summary = _summary([_team_players("26", [_skater("101")], [_skater("201")], [])])

        items, _ = nhl.boxscore_to_player_game_stats(summary, "nhl")

        assert set(items[0]["stat_line"]) == set(items[1]["stat_line"])

    def test_goalie_line_and_first_listed_goalie_is_the_starter(self):
        # A pulled starter plays less than his replacement but is still listed first.
        summary = _summary([_team_players("1", [], [], [
            _goalie("301", saves="19", shots_against="24", toi="28:33", save_pct=".792"),
            _goalie("302", saves="16", shots_against="17", toi="32:40", save_pct=".941"),
        ])], team_ids=("1", "7"))

        items, _ = nhl.boxscore_to_player_game_stats(summary, "nhl")

        starter, relief = items
        assert (starter["entity_id"], starter["started"], starter["position_group"]) == ("301", True, "goalie")
        assert (relief["entity_id"], relief["started"]) == ("302", False)
        assert starter["stat_line"]["saves"] == 19
        assert starter["stat_line"]["save_pct"] == 0.792
        assert starter["stat_line"]["time_on_ice_seconds"] == 28 * 60 + 33
        assert "points" not in starter["stat_line"]

    def test_each_team_has_its_own_starting_goalie(self):
        summary = _summary([
            _team_players("26", [], [], [_goalie("301")]),
            _team_players("16", [], [], [_goalie("401")]),
        ])

        items, _ = nhl.boxscore_to_player_game_stats(summary, "nhl")

        assert [(i["team_id"], i["started"]) for i in items] == [("26", True), ("16", True)]

    def test_scratched_players_and_stubs_without_an_id_or_stats_are_dropped(self):
        stub = {"athlete": {"displayName": "No Id"}, "stats": ["0"] * len(SKATER_KEYS)}
        no_stats = {"athlete": {"id": "103", "displayName": "DNP"}, "stats": []}
        summary = _summary([_team_players("26", [_skater("101"), _skater("102", scratched=True), stub, no_stats], [], [])])

        items, entities = nhl.boxscore_to_player_game_stats(summary, "nhl")

        assert [i["entity_id"] for i in items] == ["101"]
        assert [e["entity_id"] for e in entities] == ["101"]

    def test_unparseable_values_become_none_never_a_string(self):
        dirty = _skater("101", goals="--", toi="", shots_missed="")
        summary = _summary([_team_players("26", [dirty], [], [_goalie("301", save_pct="-", toi="DNP")])])

        items, _ = nhl.boxscore_to_player_game_stats(summary, "nhl")

        skater, goalie = items
        assert skater["stat_line"]["goals"] is None
        assert skater["stat_line"]["time_on_ice_seconds"] is None
        assert skater["stat_line"]["shots_missed"] is None
        assert "points" not in skater["stat_line"]
        assert goalie["stat_line"]["save_pct"] is None
        assert goalie["stat_line"]["time_on_ice_seconds"] is None
        for item in items:
            assert all(value is None or isinstance(value, (int, float)) for value in item["stat_line"].values())

    def test_items_and_entities_carry_keys_team_and_position(self):
        summary = _summary([_team_players("26", [_skater("101")], [], [])])

        [item], [entity] = nhl.boxscore_to_player_game_stats(summary, "nhl")

        assert item["event_key"] == "SPORT#NHL#EVENT#401803584"
        assert item["player_key"] == "SPORT#NHL#PLAYER#101"
        assert (item["team_id"], item["event_date"], item["sport"]) == ("26", "2026-04-07", "nhl")
        assert entity["entity_key"] == "SPORT#NHL#ENTITY#PLAYER#101"
        assert entity["metadata"] == {"team_id": "26", "team_id_as_of": "2026-04-07", "jersey": "9", "position": "C"}

    def test_empty_box_score_returns_nothing(self):
        assert nhl.boxscore_to_player_game_stats(_summary([]), "nhl") == ([], [])


class TestBoxscoreToTeamGameStats:
    def test_team_line_adds_totals_summed_from_its_own_players(self):
        summary = _summary(
            [
                _team_players("26", [_skater("101", shots_missed="2")], [_skater("201", shots_missed="3")], [_goalie("301", saves="29", shots_against="32")]),
                _team_players("16", [_skater("111", shots_missed="4")], [], [
                    _goalie("401", saves="10", shots_against="12"), _goalie("402", saves="5", shots_against="5"),
                ]),
            ],
            teams=[_team_block("26"), _team_block("16", shotsTotal="27")],
        )

        home, away = nhl.boxscore_to_team_game_stats(summary, "nhl")

        assert (home["team_id"], home["team_key"]) == ("26", "TEAM#26")
        assert home["stat_line"]["shots_total"] == 32
        assert home["stat_line"]["power_play_opportunities"] == 3
        assert home["stat_line"]["power_play_pct"] == 33.3
        assert (home["stat_line"]["shots_missed"], home["stat_line"]["saves"], home["stat_line"]["shots_against"]) == (5, 29, 32)
        assert (away["stat_line"]["shots_missed"], away["stat_line"]["saves"], away["stat_line"]["shots_against"]) == (4, 15, 17)
        assert away["stat_line"]["shots_total"] == 27

    def test_unparseable_team_value_becomes_none(self):
        summary = _summary([], teams=[_team_block("26", powerPlayPct="-")])

        [team] = nhl.boxscore_to_team_game_stats(summary, "nhl")

        assert team["stat_line"]["power_play_pct"] is None
        assert "shots_missed" not in team["stat_line"]


class TestProbableGoalie:
    def test_other_probables_and_entries_without_an_athlete_are_passed_over(self):
        competitor = {"probables": [
            {"name": "probableStartingPitcher", "playerId": 1},
            {"name": "probableStartingGoalie"},
            {"name": "probableStartingGoalie", "athlete": {"id": "77"}, "status": {"name": "Confirmed"}},
        ]}

        assert nhl._probable_goalie(competitor) == ("77", "Confirmed")

    def test_none_without_a_listed_goalie(self):
        assert nhl._probable_goalie({"probables": None}) == (None, None)
