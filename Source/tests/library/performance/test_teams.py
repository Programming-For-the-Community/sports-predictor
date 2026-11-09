"""Win accuracy per team: each graded game counts for both teams, over the
scorecard's games and the last 30 days."""
from datetime import date

from test_head_to_head import _row

from library.performance import teams

TODAY = date(2026, 11, 9)


def _game(event_id, home, away, home_score, away_score, event_date):
    return {
        "event_key": f"SPORT#NFL#EVENT#{event_id}", "event_date": event_date,
        "participants": [
            {"entity_id": home, "role": "home", "result": {"score": home_score}},
            {"entity_id": away, "role": "away", "result": {"score": away_score}},
        ],
    }


ENTITIES = {
    ("16", "team"): {"name": "Minnesota Vikings", "metadata": {"abbreviation": "MIN"}},
    ("9", "team"): {"name": "Green Bay Packers", "metadata": {"abbreviation": "GB"}},
    ("8", "team"): {"name": "Detroit Lions", "metadata": {"abbreviation": "DET"}},
}


def test_each_team_gets_its_games_and_its_last_30_days():
    games = [
        _game(1, "16", "9", 27, 20, "2026-09-14"),   # home picked, home won: right
        _game(2, "16", "8", 10, 24, "2026-10-26"),   # home picked, home lost: wrong
        _game(3, "9", "16", 21, 17, "2026-11-02"),   # home picked, home won: right
        _game(4, "8", "9", 30, 10, "2026-11-08"),    # no prediction logged
    ]
    graded = {g["event_key"]: [_row("MODEL#win-probability#v3", {"home_win_probability": 0.7})] for g in games[:3]}

    result = teams.by_team(games, graded, ENTITIES, TODAY)

    rows = {row["team"]: row for row in result["teams"]}
    assert rows["MIN"] == {"team": "MIN", "name": "Minnesota Vikings", "games": 3, "correct": 2, "accuracy_pct": 66.7,
                           "recent_games": 2, "recent_correct": 1}
    assert rows["DET"] == {"team": "DET", "name": "Detroit Lions", "games": 1, "correct": 0, "accuracy_pct": 0.0,
                           "recent_games": 1, "recent_correct": 0}
    assert result["teams"][0]["team"] == "MIN"
    assert result["recent_days"] == teams.RECENT_DAYS


def test_nothing_graded_gives_no_breakdown():
    assert teams.by_team([_game(1, "16", "9", 27, 20, "2026-11-01")], {}, ENTITIES, TODAY) is None
