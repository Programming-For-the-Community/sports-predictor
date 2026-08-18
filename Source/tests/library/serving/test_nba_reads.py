"""
Unit tests for library.serving.nba_reads -- list_events, list_models, and
their supporting comparison helpers. storage/predictions_table/s3 are
MagicMocks.

No TestRoundLabel here, unlike test_ncaafb_reads.py -- NBA has no
postseason-round concept at all (see nba_reads.py's own docstring).
"""
from datetime import date, timedelta
from unittest.mock import MagicMock

from library.serving import nba_reads


# "scheduled" event_dates are relative to today, not hardcoded -- list_events'
# soonest-upcoming-date scoping filters straight to today-or-later (see
# nba_reads._next_day_events), so a fixed past date would silently drop out
# of the result as real time moves forward.
def _future(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _event(event_key, event_date, home_id, away_id, status="scheduled", season=2026, home_score=None, away_score=None):
    home_won = home_score is not None and away_score is not None and home_score > away_score
    away_won = home_score is not None and away_score is not None and away_score > home_score
    return {
        "event_key": event_key, "event_id": event_key.split("#")[-1], "event_date": event_date,
        "kickoff_time": f"{event_date}T19:00:00.000Z", "status": status, "season": season,
        "participants": [
            {"entity_id": home_id, "role": "home", "result": {"score": home_score, "won": home_won}},
            {"entity_id": away_id, "role": "away", "result": {"score": away_score, "won": away_won}},
        ],
    }


class TestListEvents:
    def test_scheduled_returns_only_the_soonest_date(self):
        storage = MagicMock()
        predictions_table = MagicMock()
        storage.get_all_events.return_value = [
            _event("e1", _future(11), "13", "2"),
            _event("e2", _future(4), "13", "17"),
            _event("e3", _future(4), "9", "21"),
        ]

        result = nba_reads.list_events(storage, predictions_table, "nba", "scheduled")

        assert {e["event_id"] for e in result["events"]} == {"e2", "e3"}

    def test_participants_carry_name_and_abbreviation_from_their_entity(self):
        storage = MagicMock()
        predictions_table = MagicMock()
        storage.get_all_events.return_value = [_event("e1", _future(4), "13", "2")]
        storage.get_entity.side_effect = lambda sport, entity_id, entity_type: {
            "13": {"name": "Lakers", "metadata": {"abbreviation": "LAL"}},
            "2": {"name": "Celtics", "metadata": {"abbreviation": "BOS"}},
        }[entity_id]

        result = nba_reads.list_events(storage, predictions_table, "nba", "scheduled")

        home, away = result["events"][0]["participants"]
        assert home["abbreviation"] == "LAL"
        assert away["abbreviation"] == "BOS"

    def test_a_stale_never_played_scheduled_event_does_not_mask_a_real_upcoming_date(self):
        storage = MagicMock()
        predictions_table = MagicMock()
        stale_date = (date.today() - timedelta(days=400)).isoformat()
        storage.get_all_events.return_value = [
            _event("stale", stale_date, "13", "2", season=2024),
            _event("e2", _future(4), "13", "17"),
            _event("e3", _future(4), "9", "21"),
        ]

        result = nba_reads.list_events(storage, predictions_table, "nba", "scheduled")

        assert {e["event_id"] for e in result["events"]} == {"e2", "e3"}

    def test_a_past_dated_scheduled_event_is_excluded_even_when_another_event_is_upcoming(self):
        storage = MagicMock()
        predictions_table = MagicMock()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        storage.get_all_events.return_value = [
            _event("e1", yesterday, "13", "2"),
            _event("e2", _future(2), "13", "17"),
        ]

        result = nba_reads.list_events(storage, predictions_table, "nba", "scheduled")

        assert {e["event_id"] for e in result["events"]} == {"e2"}

    def test_completed_returns_only_the_most_recent_date(self):
        storage = MagicMock()
        predictions_table = MagicMock()
        predictions_table.query.return_value = []
        storage.get_all_events.return_value = [
            _event("e1", "2025-11-04", "13", "2", status="completed", home_score=112, away_score=108),
            _event("e2", "2025-11-11", "13", "17", status="completed", home_score=101, away_score=99),
        ]
        storage.get_player_game_stats_for_event.return_value = []

        result = nba_reads.list_events(storage, predictions_table, "nba", "completed")

        assert {e["event_id"] for e in result["events"]} == {"e2"}

    def test_empty_when_nothing_ingested_for_that_status(self):
        storage = MagicMock()
        storage.get_all_events.return_value = []
        result = nba_reads.list_events(storage, MagicMock(), "nba", "scheduled")
        assert result["events"] == []

    def test_scheduled_events_have_no_comparison_fields(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [_event("e1", _future(4), "13", "2")]
        result = nba_reads.list_events(storage, MagicMock(), "nba", "scheduled")
        assert "prediction_comparison" not in result["events"][0]

    def test_completed_event_queries_the_predictions_table_exactly_once(self):
        # prediction_comparison and leaders_comparison used to each
        # independently re-query the same event_key partition -- one
        # request per event, not two. See list_events' own comment for why.
        storage = MagicMock()
        predictions_table = MagicMock()
        predictions_table.query.return_value = [
            {"model_key": "MODEL#win-probability#v1", "predicted_value": {"home_win_probability": 0.6}},
        ]
        storage.get_all_events.return_value = [
            _event("e1", "2025-11-11", "13", "2", status="completed", home_score=112, away_score=108),
        ]
        storage.get_player_game_stats_for_event.return_value = []

        nba_reads.list_events(storage, predictions_table, "nba", "completed")

        predictions_table.query.assert_called_once()


class TestPredictionComparison:
    def test_none_when_game_has_no_final_score(self):
        event = _event("e1", "2025-11-11", "13", "2", status="scheduled")
        assert nba_reads._prediction_comparison(MagicMock(), event) is None

    def test_none_when_no_prediction_was_ever_logged(self):
        predictions_table = MagicMock()
        predictions_table.query.return_value = []
        event = _event("e1", "2025-11-11", "13", "2", status="completed", home_score=112, away_score=108)
        assert nba_reads._prediction_comparison(predictions_table.query.return_value, event) is None

    def test_compares_predicted_vs_actual(self):
        predictions_table = MagicMock()
        predictions_table.query.return_value = [
            {"model_key": "MODEL#win-probability#v1", "predicted_value": {"home_win_probability": 0.7}},
            {"model_key": "MODEL#score-margin#v1", "predicted_value": {"value": 4.0}},
            {"model_key": "MODEL#home-score#v1", "predicted_value": {"value": 110.0}},
            {"model_key": "MODEL#away-score#v1", "predicted_value": {"value": 106.0}},
        ]
        event = _event("e1", "2025-11-11", "13", "2", status="completed", home_score=112, away_score=108)

        result = nba_reads._prediction_comparison(predictions_table.query.return_value, event)

        assert result["predicted_home_won"] is True
        assert result["actual_home_won"] is True
        assert result["correct"] is True
        assert result["actual_margin"] == 4


class TestLeadersComparison:
    def test_none_when_nothing_was_ever_predicted(self):
        predictions_table = MagicMock()
        predictions_table.query.return_value = []
        event = _event("e1", "2025-11-11", "13", "2")
        assert nba_reads._leaders_comparison(MagicMock(), predictions_table.query.return_value, "nba", event) is None

    def test_scoring_leaders_are_grouped_as_a_list(self):
        storage = MagicMock()
        storage.get_entity.side_effect = lambda sport, entity_id, entity_type: {
            "p1": {"metadata": {"team_id": "13"}, "name": "Player One"},
            "p2": {"metadata": {"team_id": "13"}, "name": "Player Two"},
        }[entity_id]
        storage.get_player_game_stats_for_event.return_value = [
            {"entity_id": "p1", "stat_line": {"points": 24}},
        ]
        predictions_table = MagicMock()
        predictions_table.query.return_value = [
            {"model_key": "MODEL#player-prop-points#v1#PLAYER#p1", "predicted_value": {"value": 27.0}},
            {"model_key": "MODEL#player-prop-points#v1#PLAYER#p2", "predicted_value": {"value": 18.0}},
        ]
        event = _event("e1", "2025-11-11", "13", "2")

        result = nba_reads._leaders_comparison(storage, predictions_table.query.return_value, "nba", event)

        assert [r["entity_id"] for r in result["home"]["scoring"]] == ["p1", "p2"]  # ranked by predicted points
        assert result["home"]["scoring"][0]["actual"] == {"points": 24}
        assert result["away"]["scoring"] == []

    def test_scoring_is_capped_at_top_2_by_predicted_points(self):
        # Guards against an event whose audit trail holds more than 2
        # recorded scoring predictions -- the comparison should still only
        # ever show the top 2, same as the pre-game leaders block did.
        storage = MagicMock()
        storage.get_entity.side_effect = lambda sport, entity_id, entity_type: {"metadata": {"team_id": "13"}, "name": entity_id}
        storage.get_player_game_stats_for_event.return_value = []
        predictions_table = MagicMock()
        predictions_table.query.return_value = [
            {"model_key": f"MODEL#player-prop-points#v1#PLAYER#p{i}", "predicted_value": {"value": float(i)}}
            for i in range(1, 5)
        ]
        event = _event("e1", "2025-11-11", "13", "2")

        result = nba_reads._leaders_comparison(storage, predictions_table.query.return_value, "nba", event)

        assert len(result["home"]["scoring"]) == 2
        assert [r["entity_id"] for r in result["home"]["scoring"]] == ["p4", "p3"]

    def test_skips_a_player_who_has_since_left_both_teams(self):
        storage = MagicMock()
        predictions_table = MagicMock()
        predictions_table.query.return_value = [
            {"model_key": "MODEL#player-prop-points#v1#PLAYER#101", "predicted_value": {"value": 24.0}},
        ]
        storage.get_entity.return_value = {"metadata": {"team_id": "99"}}  # neither home nor away
        storage.get_player_game_stats_for_event.return_value = []
        event = _event("e1", "2025-11-11", "13", "2")

        result = nba_reads._leaders_comparison(storage, predictions_table.query.return_value, "nba", event)

        assert result["home"]["scoring"] == []
        assert result["away"]["scoring"] == []


class TestListModels:
    def test_empty_when_no_models_promoted(self):
        s3 = MagicMock()
        s3.list_keys.return_value = []
        assert nba_reads.list_models(s3, "nba") == {"sport": "nba", "models": []}

    def test_lists_promoted_models_with_card_summaries(self):
        s3 = MagicMock()
        s3.list_keys.return_value = ["nba/win-probability/v1/model_card.json"]
        s3.object_exists.return_value = True
        s3.get_json.side_effect = [
            {"version": 1},
            {
                "model_name": "win-probability", "algorithm": "xgboost", "version": 1,
                "trained_at": "2026-01-01T00:00:00Z", "accuracy": 0.7,
                "feature_importances": {"elo_diff": 0.3},
            },
        ]

        result = nba_reads.list_models(s3, "nba")

        assert len(result["models"]) == 1
        assert result["models"][0]["model_name"] == "win-probability"

    def test_model_never_promoted_is_excluded(self):
        s3 = MagicMock()
        s3.list_keys.return_value = ["nba/win-probability/v1/model_card.json"]
        s3.object_exists.return_value = False

        result = nba_reads.list_models(s3, "nba")

        assert result["models"] == []


class TestGetSeasonProjection:
    def test_returns_the_cached_projection_from_its_own_key(self):
        s3 = MagicMock()
        s3.object_exists.return_value = True
        s3.get_json.return_value = {"sport": "nba", "season": 2026, "standings": []}

        result = nba_reads.get_season_projection(s3, "nba")

        assert result == {"sport": "nba", "season": 2026, "standings": []}
        s3.get_json.assert_called_once_with("season-projections/nba/latest.json")

    def test_returns_none_when_the_scheduled_job_hasnt_written_one_yet(self):
        s3 = MagicMock()
        s3.object_exists.return_value = False

        assert nba_reads.get_season_projection(s3, "nba") is None
