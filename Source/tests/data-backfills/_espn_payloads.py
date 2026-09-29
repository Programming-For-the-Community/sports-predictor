"""
Hand-built ESPN API responses for the data-backfills client/normalize
tests, shaped field-for-field like the real endpoints (teams, scoreboard,
summary, golf leaderboard), plus a fake requests session so each sport's
real ESPN client runs end to end without any network access.

Not test_-prefixed, so pytest never collects it as a test module itself.
"""
from unittest.mock import MagicMock


def fake_session(routes: dict[str, dict]):
    """A requests.Session stand-in: GET <base_url>/<path> returns
    routes[path] as the JSON body. An unrouted path raises KeyError, so a
    client asking for an unexpected endpoint fails loudly."""
    session = MagicMock()

    def _get(url, params=None, timeout=None):
        path = next(p for p in sorted(routes, key=len, reverse=True) if url.endswith("/" + p))
        response = MagicMock()
        response.json.return_value = routes[path]
        return response

    session.get.side_effect = _get
    return session


def teams_payload(count: int) -> dict:
    return {"sports": [{"leagues": [{"teams": [
        {"team": {
            "id": str(i), "abbreviation": f"T{i}", "displayName": f"Team {i}", "location": f"City {i}",
            "name": f"Mascots {i}", "nickname": f"City {i}", "color": "112233",
        }}
        for i in range(1, count + 1)
    ]}]}]}


def scoreboard_event(event_id: str, date: str, home_id: str, away_id: str, home_score: str, away_score: str, season_type: int = 2) -> dict:
    return {
        "id": event_id,
        "date": date,
        "season": {"year": 2025, "type": season_type},
        "week": {"number": 1},
        "status": {"type": {"completed": True, "state": "post"}},
        "competitions": [{
            "date": date,
            "competitors": [
                {"homeAway": "home", "team": {"id": home_id}, "score": home_score, "winner": int(home_score) > int(away_score)},
                {"homeAway": "away", "team": {"id": away_id}, "score": away_score, "winner": int(away_score) > int(home_score)},
            ],
            "venue": {"fullName": "Home Arena", "indoor": True, "address": {"city": "Kansas City", "state": "MO"}},
            "weather": {"temperature": 72},
        }],
    }


def scoreboard_payload(*events: dict) -> dict:
    return {"events": list(events)}


def _athlete(athlete_id: str, name: str, position: str) -> dict:
    return {"id": athlete_id, "displayName": name, "position": {"abbreviation": position}}


def basketball_summary(event_id: str, date: str, home_id: str, away_id: str) -> dict:
    """NBA/NCAA MBB: one unnamed player stat category with compound
    made-attempted columns, and flat team statistics."""
    keys = [
        "minutes", "points", "fieldGoalsMade-fieldGoalsAttempted", "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
        "freeThrowsMade-freeThrowsAttempted", "rebounds", "offensiveRebounds", "defensiveRebounds", "assists",
    ]

    def _players(team_id, first_id):
        return {"team": {"id": team_id}, "statistics": [{"keys": keys, "athletes": [
            {"athlete": _athlete(str(first_id), f"Guard {first_id}", "G"), "stats": ["34", "27", "10-19", "3-7", "4-4", "6", "1", "5", "8"]},
            {"athlete": _athlete(str(first_id + 1), f"Center {first_id + 1}", "C"), "stats": ["30", "14", "6-9", "0-0", "2-3", "12", "4", "8", "2"]},
        ]}]}

    def _team(team_id, rebounds):
        return {"team": {"id": team_id}, "statistics": [
            {"name": "fieldGoalsMade-fieldGoalsAttempted", "displayValue": "40-85"},
            {"name": "totalRebounds", "displayValue": rebounds},
            {"name": "turnovers", "displayValue": "12"},
        ]}

    return {
        "header": {"id": event_id, "competitions": [{"date": date}]},
        "boxscore": {
            "players": [_players(home_id, 100), _players(away_id, 200)],
            "teams": [_team(home_id, "44"), _team(away_id, "39")],
        },
    }


def football_summary(event_id: str, date: str, home_id: str, away_id: str) -> dict:
    """NFL: named player stat categories (passing/rushing), one athlete
    per category here, and flat team statistics."""

    def _players(team_id, qb_id, rb_id):
        return {"team": {"id": team_id}, "statistics": [
            {"name": "passing", "keys": ["completions/passingAttempts", "passingYards", "passingTouchdowns"], "athletes": [
                {"athlete": _athlete(qb_id, f"QB {qb_id}", "QB"), "stats": ["22/31", "268", "2"]},
            ]},
            {"name": "rushing", "keys": ["rushingAttempts", "rushingYards", "rushingTouchdowns"], "athletes": [
                {"athlete": _athlete(rb_id, f"RB {rb_id}", "RB"), "stats": ["18", "94", "1"]},
            ]},
        ]}

    def _team(team_id, yards):
        return {"team": {"id": team_id}, "statistics": [
            {"name": "totalYards", "displayValue": yards},
            {"name": "thirdDownEff", "displayValue": "5-12"},
            {"name": "turnovers", "displayValue": "1"},
        ]}

    return {
        "header": {"id": event_id, "competitions": [{"date": date}]},
        "boxscore": {
            "players": [_players(home_id, "301", "302"), _players(away_id, "401", "402")],
            "teams": [_team(home_id, "380"), _team(away_id, "311")],
        },
    }


def golf_competitor(athlete_id: str, position: str, score_display: str, status_name: str = "STATUS_FINISH") -> dict:
    return {
        "id": athlete_id,
        "earnings": 150000.0,
        "amateur": False,
        "athlete": {"id": athlete_id, "displayName": f"Golfer {athlete_id}", "flag": {"alt": "USA"}, "amateur": False},
        "status": {
            "type": {"id": "2", "name": status_name, "state": "post", "completed": True},
            "position": {"id": position.lstrip("T"), "displayName": position, "isTie": position.startswith("T")},
        },
        "score": {"value": 272.0, "displayValue": score_display},
        "linescores": [
            {"period": 1, "value": 68.0, "displayValue": "-4"},
            {"period": 2, "value": 69.0, "displayValue": "-3"},
            {"period": 3, "value": 67.0, "displayValue": "-5"},
            {"period": 4, "value": 68.0, "displayValue": "-4"},
        ],
    }


def golf_leaderboard_event(event_id: str, competitors: list[dict]) -> dict:
    return {
        "id": event_id,
        "date": "2021-02-11T08:00Z",
        "endDate": "2021-02-14T08:00Z",
        "season": {"year": 2021},
        "seasonType": {"id": "2", "name": "Regular Season"},
        "week": {},
        "purse": 7800000,
        "tournament": {"displayName": "AT&T Pebble Beach Pro-Am", "major": False, "scoringSystem": {"name": "Medal"}},
        "status": {"type": {"name": "STATUS_FINAL", "completed": True, "state": "post"}},
        "courses": [{
            "id": "31", "name": "Pebble Beach Golf Links", "host": True, "shotsToPar": 72,
            "address": {"city": "Pebble Beach", "state": "CA", "country": "USA"},
        }],
        "competitions": [{"competitors": competitors}],
    }
