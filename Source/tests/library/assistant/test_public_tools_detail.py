"""Public tools: games on a date, player detail, fields, season views and model info."""
import json

from library.assistant import public_tools
from library.assistant.settings import ToolSettings

SPORTS = ["f1", "nba", "ncaambb", "nfl", "pga"]
SETTINGS = ToolSettings(max_rows=25, max_result_chars=12_000, max_trend_points=400, hourly_trend_days=7, repeat_min_blocks=100, repeat_min_days=3)


class FakeReader:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, resource, path_params, query_params):
        self.calls.append((resource, path_params, query_params))
        response = self.responses[resource]
        return response(query_params) if callable(response) else response


def _game(event_id, status, leaders=None):
    return {"event_id": event_id, "status": status, "participants": [{"role": "home", "abbreviation": f"H{event_id}"}],
            "leaders_comparison": leaders}


def test_a_date_lists_that_day_s_finished_and_unfinished_games():
    by_status = {"completed": [_game("1", "completed")], "scheduled": [_game("2", "scheduled")]}
    read = FakeReader({"/nba/events": lambda query: (200, {"events": by_status[query["status"]]})})

    result = public_tools.run("list_events", {"sport": "nba", "date": "2026-11-01"}, read, SPORTS, SETTINGS)

    assert [c[2] for c in read.calls] == [{"status": "completed", "date": "2026-11-01"}, {"status": "scheduled", "date": "2026-11-01"}]
    assert result["date"] == "2026-11-01"
    assert "when" not in result
    assert [e["event_id"] for e in result["events"]] == ["1", "2"]


def test_a_sport_that_can_t_list_by_date_gets_the_reason():
    read = FakeReader({"/pga/events": (400, {"error": "This sport's events can't be listed by date."})})
    result = public_tools.run("list_events", {"sport": "pga", "date": "2026-11-01"}, read, SPORTS, SETTINGS)
    assert result == {"error": "This sport's events can't be listed by date."}


def test_a_short_list_keeps_player_predicted_vs_actual_and_a_long_one_drops_it():
    leaders = {"home": {"points": [{"name": "A Scorer", "predicted": 24.4, "actual": 31}]}}
    few = [_game(str(i), "completed", leaders) for i in range(public_tools.PLAYER_DETAIL_MAX_GAMES)]
    many = [_game(str(i), "completed", leaders) for i in range(public_tools.PLAYER_DETAIL_MAX_GAMES + 1)]

    short = public_tools.run("list_events", {"sport": "nba", "when": "recent"}, FakeReader({"/nba/events": (200, {"events": few})}), SPORTS, SETTINGS)
    long = public_tools.run("list_events", {"sport": "nba", "when": "recent"}, FakeReader({"/nba/events": (200, {"events": many})}), SPORTS, SETTINGS)

    assert short["events"][0]["leaders_comparison"]["home"]["points"][0]["actual"] == 31
    assert "note" not in short
    assert "leaders_comparison" not in json.dumps(long["events"])
    assert "filter by team" in long["note"]


def test_stored_predictions_read_as_plain_numbers():
    assert public_tools.prune({"margin": {"value": 3.14159, "model_version": 4}, "win": {"home_win_probability": 0.6, "model_version": 2}}) == \
        {"margin": 3.142, "win": {"home_win_probability": 0.6}}


def test_a_field_is_cut_in_predicted_order_and_can_be_narrowed_to_one_player():
    field = [{"entity_id": str(i), "name": f"Golfer {i}", "predictions": {"projected_score_to_par": {"value": -20 + i, "model_version": 6}}}
             for i in range(40)]
    read = FakeReader({"/pga/predictions/events/{event_id}": (200, {"result": {"tournament_name": "Open", "field": field}})})

    whole = public_tools.run("get_event_prediction", {"sport": "pga", "event_id": "9"}, read, SPORTS, SETTINGS)
    assert [g["name"] for g in whole["field"][:2]] == ["Golfer 0", "Golfer 1"]
    assert "25 of 40" in whole["field_note"]

    one = public_tools.run("get_event_prediction", {"sport": "pga", "event_id": "9", "player": "golfer 33"}, read, SPORTS, SETTINGS)
    assert one["field"] == [{"name": "Golfer 33", "predictions": {"projected_score_to_par": 13}}]
    assert "field_note" not in one


def test_the_player_filter_reaches_team_sport_leader_lists():
    leaders = {"home": {"points": [{"name": "A Scorer", "points": 24.0}, {"name": "B Shooter", "points": 18.0}]}, "away": {"points": []}}
    read = FakeReader({"/nba/predictions/events/{event_id}": (200, {"result": {"leaders": leaders}})})
    result = public_tools.run("get_event_prediction", {"sport": "nba", "event_id": "5", "player": "shooter"}, read, SPORTS, SETTINGS)
    assert result["leaders"]["home"]["points"] == [{"name": "B Shooter", "points": 18.0}]


def test_season_projection_views():
    body = {"season": 2026, "standings": [{"team_id": "1", "name": "Buffalo Bills", "abbreviation": "BUF", "projected_wins": 12.0}],
            "bracket": {"rounds": [{"round": "Final", "matchups": [{"team_a": "1", "team_b": None, "seed_a": 1, "predicted_winner": "1"}]}],
                        "champion": "1", "team_names": {"1": {"abbreviation": "BUF"}}}}
    read = FakeReader({"/nfl/season": (200, body)})

    standings = public_tools.run("get_season_projection", {"sport": "nfl"}, read, SPORTS, SETTINGS)
    bracket = public_tools.run("get_season_projection", {"sport": "nfl", "view": "bracket"}, read, SPORTS, SETTINGS)

    assert standings["standings"]["rows"][0]["abbreviation"] == "BUF"
    assert "bracket" not in standings
    assert bracket["brackets"]["Playoffs"]["matchups"][0]["matchup"] == "BUF (1) vs bye"
    assert "typical number of upsets" in bracket["note"]


def test_model_info_reports_version_training_and_main_inputs():
    card = {"model_name": "win-probability", "algorithm": "xgboost", "version": 7, "trained_at": "2026-11-01T10:00:00Z",
            "accuracy": 0.6612, "naive_baseline_accuracy": 0.571, "top_features": [{"feature": "elo_diff", "importance": 0.4}],
            "candidates": [{"name": f"c{i}"} for i in range(6)]}
    read = FakeReader({"/nfl/models": (200, {"models": [card, {"model_name": "score-margin", "version": 3}]})})

    one = public_tools.run("get_model_info", {"sport": "nfl", "model": "win-probability"}, read, SPORTS, SETTINGS)
    assert one["models"][0]["top_features"] == ["elo_diff"]
    assert one["models"][0]["accuracy"] == 0.661
    assert len(one["models"][0]["candidates"]) == public_tools.MAX_CANDIDATES
    missing = public_tools.run("get_model_info", {"sport": "nfl", "model": "nope"}, read, SPORTS, SETTINGS)
    assert missing["models"] == ["score-margin", "win-probability"]
