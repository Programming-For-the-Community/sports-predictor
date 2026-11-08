"""Win accuracy by conference: each graded game counts for each team's
conference, conference games are also totalled, and small conferences and
conference-less sports are left out."""
from test_head_to_head import _row

from library.performance import conferences

CONFERENCE_OF = {"duke": "ACC", "unc": "ACC", "uk": "SEC", "uga": "SEC", "nfl-team": None}


def _game(event_id, home, away, home_score, away_score):
    return {
        "event_key": f"SPORT#NCAAMBB#EVENT#{event_id}",
        "participants": [
            {"entity_id": home, "role": "home", "result": {"score": home_score}},
            {"entity_id": away, "role": "away", "result": {"score": away_score}},
        ],
    }


def _entities():
    return {(team, "team"): {"metadata": {"conference": conf} if conf else {}} for team, conf in CONFERENCE_OF.items()}


def _picked_home(event_key):
    return {event_key: [_row("MODEL#win-probability#v3", {"home_win_probability": 0.7})]}


def _graded(games):
    rows = {}
    for game in games:
        rows.update(_picked_home(game["event_key"]))
    return rows


def test_games_count_for_each_conference_and_conference_games_are_totalled():
    games = [
        _game(1, "duke", "unc", 80, 70),   # ACC v ACC, home picked, home won
        _game(2, "unc", "duke", 60, 75),   # ACC v ACC, home picked, home lost
        _game(3, "duke", "uk", 90, 85),    # ACC v SEC, right
        _game(4, "uk", "uga", 70, 65),     # SEC v SEC, right
        _game(5, "uga", "uk", 70, 71),     # SEC v SEC, wrong
    ]

    result = conferences.by_conference(games, _graded(games), _entities())

    acc = next(r for r in result["conferences"] if r["conference"] == "ACC")
    sec = next(r for r in result["conferences"] if r["conference"] == "SEC")
    assert (acc["games"], acc["correct"], acc["accuracy_pct"]) == (3, 2, 66.7)
    assert (sec["games"], sec["correct"]) == (3, 2)
    assert result["conference_games"] == {"conference": "Conference games", "games": 4, "correct": 2, "accuracy_pct": 50.0}
    assert result["model"] == "win-probability"


def test_ungraded_games_small_conferences_and_conference_less_sports_are_left_out():
    unpredicted = _game(6, "duke", "unc", 80, 70)
    one_game = [_game(7, "uk", "uga", 70, 60)]
    nfl = [_game(8, "nfl-team", "nfl-team", 20, 17)]

    assert conferences.by_conference([unpredicted], {}, _entities()) is None
    assert conferences.by_conference(one_game, _graded(one_game), _entities()) is None
    assert conferences.by_conference(nfl, _graded(nfl), _entities()) is None


def test_team_refs_cover_every_participant_once():
    games = [_game(1, "duke", "unc", 1, 0), _game(2, "unc", "uk", 1, 0)]
    assert conferences.team_refs(games) == [("duke", "team"), ("uk", "team"), ("unc", "team")]
