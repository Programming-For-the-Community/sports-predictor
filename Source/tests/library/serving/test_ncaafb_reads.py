"""
Unit tests for library.serving.ncaafb_reads -- list_events, list_models,
and their supporting comparison/round-label helpers. NOT a port of
test_nfl_reads_*.py's file split -- consolidated here since ncaafb_reads
is a smaller module. storage/predictions_table/s3 are MagicMocks.
"""
from datetime import date, timedelta
from unittest.mock import MagicMock

from library.serving import ncaafb_reads

# "scheduled" event_dates are relative to today, not hardcoded -- list_events'
# soonest-upcoming-week scoping ignores anything more than a few days in the
# past (see ncaafb_reads._STALE_SCHEDULED_GRACE_DAYS), so a fixed past date
# would silently drop out of the result as real time moves forward.
def _future(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _event(event_key, event_date, home_id, away_id, status="scheduled", season=2025, season_type="regular", week=5,
           home_score=None, away_score=None, is_playoff_game=False):
    home_won = home_score is not None and away_score is not None and home_score > away_score
    away_won = home_score is not None and away_score is not None and away_score > home_score
    return {
        "event_key": event_key, "event_id": event_key.split("#")[-1], "event_date": event_date,
        "kickoff_time": f"{event_date}T19:00:00.000Z", "status": status,
        "season": season, "season_type": season_type, "week": week, "is_playoff_game": is_playoff_game,
        "participants": [
            {"entity_id": home_id, "role": "home", "result": {"score": home_score, "won": home_won}},
            {"entity_id": away_id, "role": "away", "result": {"score": away_score, "won": away_won}},
        ],
    }


class TestRoundLabel:
    def test_none_for_regular_season(self):
        assert ncaafb_reads._round_label(_event("e1", "2025-10-11", "61", "52", season_type="regular")) is None

    def test_cfp_for_a_real_playoff_game(self):
        event = _event("e1", "2026-01-01", "61", "52", season_type="postseason", is_playoff_game=True)
        assert ncaafb_reads._round_label(event) == "CFP"

    def test_bowl_for_a_non_playoff_postseason_game(self):
        event = _event("e1", "2025-12-28", "61", "52", season_type="postseason", is_playoff_game=False)
        assert ncaafb_reads._round_label(event) == "Bowl"


class TestListEvents:
    def test_scheduled_returns_only_the_soonest_week(self):
        storage = MagicMock()
        predictions_table = MagicMock()
        storage.get_all_events.return_value = [
            _event("e1", _future(11), "61", "52", week=6),
            _event("e2", _future(4), "61", "70", week=5),
            _event("e3", _future(4), "80", "90", week=5),
        ]

        result = ncaafb_reads.list_events(storage, predictions_table, "ncaafb", "scheduled")

        assert {e["event_id"] for e in result["events"]} == {"e2", "e3"}

    def test_participants_carry_name_and_abbreviation_from_their_entity(self):
        storage = MagicMock()
        predictions_table = MagicMock()
        storage.get_all_events.return_value = [_event("e1", _future(4), "333", "61", week=5)]
        storage.get_entity.side_effect = lambda sport, entity_id, entity_type: {
            "333": {"name": "Alabama", "metadata": {"abbreviation": "ALA", "conference": "SEC"}},
            "61": {"name": "Georgia", "metadata": {"abbreviation": "UGA", "conference": "SEC"}},
        }[entity_id]

        result = ncaafb_reads.list_events(storage, predictions_table, "ncaafb", "scheduled")

        home, away = result["events"][0]["participants"]
        assert home["abbreviation"] == "ALA"
        assert away["abbreviation"] == "UGA"
        assert home["conference"] == "SEC"

    def test_a_stale_never_played_scheduled_event_does_not_mask_a_real_upcoming_week(self):
        storage = MagicMock()
        predictions_table = MagicMock()
        stale_date = (date.today() - timedelta(days=400)).isoformat()
        storage.get_all_events.return_value = [
            _event("stale", stale_date, "61", "52", week=1, season=2024),
            _event("e2", _future(4), "61", "70", week=5),
            _event("e3", _future(4), "80", "90", week=5),
        ]

        result = ncaafb_reads.list_events(storage, predictions_table, "ncaafb", "scheduled")

        assert {e["event_id"] for e in result["events"]} == {"e2", "e3"}

    def test_a_past_dated_scheduled_event_is_excluded_even_within_the_target_week(self):
        # e1 is within _STALE_SCHEDULED_GRACE_DAYS so it still counts
        # toward picking week 5 as the target -- but "upcoming" must never
        # include a past-dated event, played or not, so it's excluded from
        # the final result while e2 (same week, future-dated) stays.
        storage = MagicMock()
        predictions_table = MagicMock()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        storage.get_all_events.return_value = [
            _event("e1", yesterday, "61", "52", week=5),
            _event("e2", _future(2), "61", "70", week=5),
        ]

        result = ncaafb_reads.list_events(storage, predictions_table, "ncaafb", "scheduled")

        assert {e["event_id"] for e in result["events"]} == {"e2"}

    def test_a_week_spanning_two_separate_clusters_only_shows_the_soonest_one(self):
        # Regression: confirmed live, 2026-08-24, against the real 2026
        # schedule -- CFBD's own week=1 tag covered a handful of true
        # season-opener games (real "Week 0" by fan convention, here on
        # days 2-3) AND the following weekend's much larger main slate
        # (here starting day 6), sharing CFBD's own week=1 tag. The two
        # clusters are only 3 days apart (day 3 -> day 6) -- close enough
        # that a first-attempt fixed span-from-the-soonest-date cap still
        # merged them, since day 6 falls inside any span wide enough to
        # hold days 2-3 together. Gap-based clustering (cut off at the
        # first gap wider than _MAX_INTRA_WEEK_GAP_DAYS) is what actually
        # separates them.
        storage = MagicMock()
        predictions_table = MagicMock()
        storage.get_all_events.return_value = [
            _event("opener1", _future(2), "61", "52", week=1),
            _event("opener2", _future(3), "80", "90", week=1),
            _event("main1", _future(6), "61", "70", week=1),
            _event("main2", _future(7), "80", "70", week=1),
        ]

        result = ncaafb_reads.list_events(storage, predictions_table, "ncaafb", "scheduled")

        assert {e["event_id"] for e in result["events"]} == {"opener1", "opener2"}

    def test_a_normal_single_cluster_week_is_unaffected_by_gap_clustering(self):
        # A normal week's own internal gaps (here 2 days, the largest
        # confirmed live short of the week-1 split above) must not get
        # split apart the way the two real clusters above should be.
        storage = MagicMock()
        predictions_table = MagicMock()
        storage.get_all_events.return_value = [
            _event("e1", _future(2), "61", "52", week=2),
            _event("e2", _future(4), "80", "90", week=2),
        ]

        result = ncaafb_reads.list_events(storage, predictions_table, "ncaafb", "scheduled")

        assert {e["event_id"] for e in result["events"]} == {"e1", "e2"}

    def test_completed_returns_only_the_most_recent_week(self):
        storage = MagicMock()
        predictions_table = MagicMock()
        predictions_table.query.return_value = []
        storage.get_all_events.return_value = [
            _event("e1", "2025-10-04", "61", "52", status="completed", week=4, home_score=24, away_score=17),
            _event("e2", "2025-10-11", "61", "70", status="completed", week=5, home_score=31, away_score=10),
        ]
        storage.get_player_game_stats_for_event.return_value = []

        result = ncaafb_reads.list_events(storage, predictions_table, "ncaafb", "completed")

        assert {e["event_id"] for e in result["events"]} == {"e2"}

    def test_completed_queries_get_all_events_most_recent_first_and_bounded(self):
        # Regression: an unbounded get_all_events call here pulled a
        # sport's entire completed-event history on every request, a real
        # production 504 confirmed live 2026-09-02.
        storage = MagicMock()
        predictions_table = MagicMock()
        storage.get_all_events.return_value = []

        ncaafb_reads.list_events(storage, predictions_table, "ncaafb", "completed")

        storage.get_all_events.assert_called_once_with(
            "ncaafb", status="completed", limit=ncaafb_reads.RECENT_EVENTS_LIMIT,
        )

    def test_scheduled_queries_get_all_events_soonest_first_and_bounded(self):
        storage = MagicMock()
        predictions_table = MagicMock()
        storage.get_all_events.return_value = []

        ncaafb_reads.list_events(storage, predictions_table, "ncaafb", "scheduled")

        storage.get_all_events.assert_called_once_with(
            "ncaafb", status="scheduled", scan_index_forward=True, limit=ncaafb_reads.RECENT_EVENTS_LIMIT,
        )

    def test_postseason_completed_events_group_by_date_not_the_flat_week_number(self):
        # Regression: CFBD's postseason `week` is flat (every bowl/CFP
        # round in a season comes back as week=1) -- confirmed live an
        # earlier CFP round was showing up alongside the actual
        # championship game because both shared week=1, 11 days apart.
        # event_date isolates each round correctly instead.
        storage = MagicMock()
        predictions_table = MagicMock()
        predictions_table.query.return_value = []
        storage.get_all_events.return_value = [
            _event("semifinal", "2026-01-09", "145", "2390", status="completed", season_type="postseason",
                   week=1, is_playoff_game=True, home_score=27, away_score=31),
            _event("championship", "2026-01-20", "84", "2390", status="completed", season_type="postseason",
                   week=1, is_playoff_game=True, home_score=27, away_score=21),
        ]
        storage.get_player_game_stats_for_event.return_value = []

        result = ncaafb_reads.list_events(storage, predictions_table, "ncaafb", "completed")

        assert {e["event_id"] for e in result["events"]} == {"championship"}

    def test_empty_when_nothing_ingested_for_that_status(self):
        storage = MagicMock()
        storage.get_all_events.return_value = []
        result = ncaafb_reads.list_events(storage, MagicMock(), "ncaafb", "scheduled")
        assert result["events"] == []

    def test_scheduled_events_have_no_comparison_fields(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [_event("e1", _future(4), "61", "52")]
        result = ncaafb_reads.list_events(storage, MagicMock(), "ncaafb", "scheduled")
        assert "prediction_comparison" not in result["events"][0]

    def test_completed_event_queries_the_predictions_table_exactly_once(self):
        # prediction_comparison and leaders_comparison used to each
        # independently re-query the same event_key partition -- one
        # request per event, not two, and (with several completed events,
        # up to 60+ FBS games in an NCAAFB week) concurrently, not a long
        # fully-serialized chain. See list_events' own comment for why.
        storage = MagicMock()
        predictions_table = MagicMock()
        predictions_table.query.return_value = [
            {"model_key": "MODEL#win-probability#v1", "predicted_value": {"home_win_probability": 0.6}},
        ]
        storage.get_all_events.return_value = [
            _event("e1", "2025-10-11", "61", "52", status="completed", home_score=24, away_score=17),
        ]
        storage.get_player_game_stats_for_event.return_value = []

        ncaafb_reads.list_events(storage, predictions_table, "ncaafb", "completed")

        predictions_table.query.assert_called_once()


class TestPredictionComparison:
    def test_none_when_game_has_no_final_score(self):
        event = _event("e1", "2025-10-11", "61", "52", status="scheduled")
        assert ncaafb_reads._prediction_comparison(MagicMock(), event) is None

    def test_none_when_no_prediction_was_ever_logged(self):
        predictions_table = MagicMock()
        predictions_table.query.return_value = []
        event = _event("e1", "2025-10-11", "61", "52", status="completed", home_score=24, away_score=17)
        assert ncaafb_reads._prediction_comparison(predictions_table.query.return_value, event) is None

    def test_compares_predicted_vs_actual(self):
        predictions_table = MagicMock()
        predictions_table.query.return_value = [
            {"model_key": "MODEL#win-probability#v1", "predicted_value": {"home_win_probability": 0.7}},
            {"model_key": "MODEL#score-margin#v1", "predicted_value": {"value": 10.0}},
            {"model_key": "MODEL#home-score#v1", "predicted_value": {"value": 27.0}},
            {"model_key": "MODEL#away-score#v1", "predicted_value": {"value": 17.0}},
        ]
        event = _event("e1", "2025-10-11", "61", "52", status="completed", home_score=24, away_score=17)

        result = ncaafb_reads._prediction_comparison(predictions_table.query.return_value, event)

        assert result["predicted_home_won"] is True
        assert result["actual_home_won"] is True
        assert result["correct"] is True
        assert result["actual_margin"] == 7


class TestLeadersComparison:
    def test_none_when_nothing_was_ever_predicted(self):
        predictions_table = MagicMock()
        predictions_table.query.return_value = []
        event = _event("e1", "2025-10-11", "61", "52")
        assert ncaafb_reads._leaders_comparison(MagicMock(), predictions_table.query.return_value, "ncaafb", event) is None

    def test_single_passing_entry_per_team(self):
        storage = MagicMock()
        predictions_table = MagicMock()
        predictions_table.query.return_value = [
            {"model_key": "MODEL#player-prop-passing-yards#v1#PLAYER#101", "predicted_value": {"value": 275.0}},
        ]
        storage.get_entity.return_value = {"metadata": {"team_id": "61"}, "name": "Carson Beck"}
        storage.get_player_game_stats_for_event.return_value = [
            {"entity_id": "101", "stat_line": {"passing_yards": 260}},
        ]
        event = _event("e1", "2025-10-11", "61", "52")

        result = ncaafb_reads._leaders_comparison(storage, predictions_table.query.return_value, "ncaafb", event)

        assert result["home"]["passing"]["entity_id"] == "101"
        assert result["home"]["passing"]["predicted"] == {"passing_yards": 275.0}
        assert result["home"]["passing"]["actual"] == {"passing_yards": 260}
        assert result["home"]["receiving"] == []
        assert result["away"]["passing"] is None

    def test_rushing_leaders_are_grouped_as_a_list(self):
        storage = MagicMock()
        storage.get_entity.side_effect = lambda sport, entity_id, entity_type: {
            "rb1": {"metadata": {"team_id": "61"}, "name": "RB One"},
            "rb2": {"metadata": {"team_id": "61"}, "name": "RB Two"},
        }[entity_id]
        storage.get_player_game_stats_for_event.return_value = []
        predictions_table = MagicMock()
        predictions_table.query.return_value = [
            {"model_key": "MODEL#player-prop-rushing-yards#v1#PLAYER#rb1", "predicted_value": {"value": 90.0}},
            {"model_key": "MODEL#player-prop-rushing-yards#v1#PLAYER#rb2", "predicted_value": {"value": 40.0}},
        ]
        event = _event("e1", "2025-10-11", "61", "52")

        result = ncaafb_reads._leaders_comparison(storage, predictions_table.query.return_value, "ncaafb", event)

        assert [r["entity_id"] for r in result["home"]["rushing"]] == ["rb1", "rb2"]  # ranked by predicted yards
        assert result["away"]["rushing"] == []

    def test_rushing_is_capped_at_top_2_by_predicted_yards(self):
        # Guards against an event whose audit trail holds more than 2 recorded
        # rushing predictions -- the comparison should still only ever show
        # the top 2, same as the pre-game leaders block did.
        storage = MagicMock()
        storage.get_entity.side_effect = lambda sport, entity_id, entity_type: {
            "metadata": {"team_id": "61"}, "name": entity_id,
        }
        storage.get_player_game_stats_for_event.return_value = []
        predictions_table = MagicMock()
        predictions_table.query.return_value = [
            {"model_key": f"MODEL#player-prop-rushing-yards#v1#PLAYER#rb{i}", "predicted_value": {"value": float(i)}}
            for i in range(1, 5)
        ]
        event = _event("e1", "2025-10-11", "61", "52")

        result = ncaafb_reads._leaders_comparison(storage, predictions_table.query.return_value, "ncaafb", event)

        assert len(result["home"]["rushing"]) == 2
        assert [r["entity_id"] for r in result["home"]["rushing"]] == ["rb4", "rb3"]

    def test_skips_a_player_who_has_since_left_both_teams(self):
        storage = MagicMock()
        predictions_table = MagicMock()
        predictions_table.query.return_value = [
            {"model_key": "MODEL#player-prop-passing-yards#v1#PLAYER#101", "predicted_value": {"value": 275.0}},
        ]
        storage.get_entity.return_value = {"metadata": {"team_id": "99"}}  # neither home nor away
        storage.get_player_game_stats_for_event.return_value = []
        event = _event("e1", "2025-10-11", "61", "52")

        result = ncaafb_reads._leaders_comparison(storage, predictions_table.query.return_value, "ncaafb", event)

        assert result["home"]["passing"] is None
        assert result["away"]["passing"] is None


class TestListModels:
    def test_empty_when_no_models_promoted(self):
        s3 = MagicMock()
        s3.list_keys.return_value = []
        assert ncaafb_reads.list_models(s3, "ncaafb") == {"sport": "ncaafb", "models": []}

    def test_lists_promoted_models_with_card_summaries(self):
        s3 = MagicMock()
        s3.list_keys.return_value = ["ncaafb/win-probability/v1/model_card.json"]
        s3.object_exists.return_value = True
        s3.get_json.side_effect = [
            {"version": 1},
            {
                "model_name": "win-probability", "algorithm": "xgboost", "version": 1,
                "trained_at": "2026-01-01T00:00:00Z", "accuracy": 0.7,
                "feature_importances": {"elo_diff": 0.3},
            },
        ]

        result = ncaafb_reads.list_models(s3, "ncaafb")

        assert len(result["models"]) == 1
        assert result["models"][0]["model_name"] == "win-probability"

    def test_model_never_promoted_is_excluded(self):
        s3 = MagicMock()
        s3.list_keys.return_value = ["ncaafb/win-probability/v1/model_card.json"]
        s3.object_exists.return_value = False

        result = ncaafb_reads.list_models(s3, "ncaafb")

        assert result["models"] == []


class TestGetSeasonProjection:
    def test_returns_none_when_not_yet_cached(self):
        s3 = MagicMock()
        s3.object_exists.return_value = False

        assert ncaafb_reads.get_season_projection(s3, "ncaafb") is None

    def test_reads_the_cached_object_under_the_expected_key(self):
        s3 = MagicMock()
        s3.object_exists.return_value = True
        s3.get_json.return_value = {"sport": "ncaafb", "standings": []}

        result = ncaafb_reads.get_season_projection(s3, "ncaafb")

        assert result == {"sport": "ncaafb", "standings": []}
        s3.object_exists.assert_called_once_with("season-projections/ncaafb/latest.json")
