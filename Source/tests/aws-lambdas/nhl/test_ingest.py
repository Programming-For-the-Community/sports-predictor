"""
Unit tests for the NHL ingest Lambda handler: date resolution, the
preseason/exhibition skip, the teams/roster refresh, box-score
idempotency/failure isolation, and injury attachment onto scoreboard
events. All AWS and ESPN calls are mocked.

The nhl_ingest module is registered in sys.modules by conftest.py, which
also sets RAW_BUCKET_NAME before the module is imported.
"""
import json
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

import nhl_ingest


def _teams_response(team_ids=()):
    return {"sports": [{"leagues": [{"teams": [{"team": {"id": tid}} for tid in team_ids]}]}]}


def _event(event_id="401803584", completed=False, season_type=2, season_year=2027, home_id="13", away_id="2"):
    return {
        "id": event_id,
        "season": {"year": season_year, "type": season_type},
        "status": {"type": {"completed": completed}},
        "competitions": [{"competitors": [
            {"team": {"id": home_id}, "homeAway": "home"},
            {"team": {"id": away_id}, "homeAway": "away"},
        ]}],
    }


def _roster(team_id, injuries_by_athlete=None):
    return {
        "team": {"id": team_id}, "timestamp": "2026-10-09T21:31:12Z",
        "athletes": [{"position": "Centers", "items": [
            {"id": athlete_id, "displayName": "Player", "injuries": [{"status": status, "date": "2026-10-07T18:11Z"}]}
            for athlete_id, status in (injuries_by_athlete or {}).items()
        ]}],
    }


def _client(events, team_ids=(), rosters=None):
    client = MagicMock()
    client.get_teams.return_value = _teams_response(team_ids)
    client.get_scoreboard_for_date.return_value = {"events": events}
    if rosters is not None:
        client.get_roster.side_effect = lambda team_id: rosters[team_id]
    return client


def _run(client, s3=None, event=None):
    with patch.object(nhl_ingest, "NHLClient", return_value=client), \
         patch.object(nhl_ingest, "_s3", s3 or MagicMock()):
        return nhl_ingest.lambda_handler(event if event is not None else {"date": "20261009"}, None)


def _written_keys(s3) -> list[str]:
    return [c.kwargs["Key"] for c in s3.put_object.call_args_list]


def _missing_object_s3():
    s3 = MagicMock()
    s3.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
    return s3


class TestDateResolution:
    def test_uses_explicit_date_override(self):
        client = _client([])

        _run(client, event={"date": "20261009"})

        client.get_scoreboard_for_date.assert_called_once_with("20261009")

    def test_defaults_to_yesterday(self):
        client = _client([])

        with patch.object(nhl_ingest, "_yesterday", return_value="20261008"):
            _run(client, event={})

        client.get_scoreboard_for_date.assert_called_once_with("20261008")


class TestSkippedGames:
    def test_preseason_date_writes_no_scoreboard_and_fetches_no_box_score(self):
        client = _client([_event(season_type=1, completed=True)])
        s3 = MagicMock()

        result = _run(client, s3)

        assert result == {"processed": 0, "skipped": 0, "failed": 0, "rosters_fetched": 0, "rosters_failed": 0}
        assert not any("/scoreboard/" in key for key in _written_keys(s3))
        client.get_summary.assert_not_called()

    def test_all_star_game_is_never_fetched(self):
        client = _client([_event(completed=True, home_id="129030", away_id="129031")])

        result = _run(client, _missing_object_s3())

        assert result["processed"] == 0
        client.get_summary.assert_not_called()

    def test_empty_scoreboard_does_not_crash(self):
        result = _run(_client([]))

        assert (result["processed"], result["skipped"], result["failed"]) == (0, 0, 0)


class TestTeamsAndRosters:
    def test_teams_and_every_roster_are_written_even_with_no_games(self):
        client = _client([], team_ids=("1", "2"), rosters={"1": _roster("1"), "2": _roster("2")})
        s3 = MagicMock()

        result = _run(client, s3)

        client.get_teams.assert_called_once()
        assert result["rosters_fetched"] == 2
        assert {"nhl/teams.json", "nhl/roster/1.json", "nhl/roster/2.json"} <= set(_written_keys(s3))

    def test_one_roster_failure_does_not_block_the_others(self):
        client = _client([], team_ids=("1", "2"))
        client.get_roster.side_effect = [Exception("boom"), _roster("2")]

        result = _run(client)

        assert (result["rosters_fetched"], result["rosters_failed"]) == (1, 1)


class TestInjuries:
    def test_hockey_injury_statuses_are_attached_to_the_written_scoreboard(self):
        rosters = {
            "13": _roster("13", {"a1": "Injured Reserve", "a2": "Day-To-Day", "a3": "Probable"}),
            "2": _roster("2", {"b1": "Out"}),
        }
        client = _client([_event()], team_ids=("13", "2"), rosters=rosters)
        s3 = MagicMock()

        _run(client, s3)

        scoreboard_call = next(c for c in s3.put_object.call_args_list if c.kwargs["Key"] == "nhl/scoreboard/20261009.json")
        [written_event] = json.loads(scoreboard_call.kwargs["Body"])["events"]
        assert written_event["home_injuries"] == [
            {"entity_id": "a1", "status": "Injured Reserve"}, {"entity_id": "a2", "status": "Day-To-Day"},
        ]
        assert written_event["away_injuries"] == [{"entity_id": "b1", "status": "Out"}]


class TestBoxScores:
    def test_incomplete_event_is_skipped(self):
        client = _client([_event(completed=False)])

        result = _run(client)

        assert result["skipped"] == 1
        client.get_summary.assert_not_called()

    def test_completed_event_is_fetched_and_written_under_its_season(self):
        client = _client([_event(event_id="401803584", completed=True, season_year=2027)])
        client.get_summary.return_value = {"header": {"id": "401803584"}}
        s3 = _missing_object_s3()

        result = _run(client, s3)

        assert result["processed"] == 1
        assert "nhl/boxscore/2027/401803584.json" in _written_keys(s3)

    def test_existing_box_score_is_not_refetched(self):
        client = _client([_event(completed=True)])

        result = _run(client, MagicMock())  # head_object succeeds -> already in S3

        assert result["skipped"] == 1
        client.get_summary.assert_not_called()

    def test_one_summary_failure_does_not_block_the_others(self):
        client = _client([_event(event_id="1", completed=True), _event(event_id="2", completed=True)])
        client.get_summary.side_effect = [Exception("boom"), {"header": {"id": "2"}}]

        result = _run(client, _missing_object_s3())

        assert (result["processed"], result["failed"]) == (1, 1)
