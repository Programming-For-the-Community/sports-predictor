"""
Unit tests for data-backfills/pga/backfill.py's own logic -- everything
here is mocked, no real ESPN calls. Covers the season-batching, the
one-call calendar discovery (June 1 of the season's label year), the
idempotent per-tournament skip, the non-stroke-play-tournament skip
(Ryder Cup/Presidents Cup/WGC Match Play/Zurich Classic -- see
library.normalize.pga.is_medal_scoring's own docstring for the confirmed-
live crash this guards against), and per-tournament failure isolation.

The backfill module is importable directly (conftest.py inserts its
directory onto sys.path).
"""
from unittest.mock import MagicMock, patch

import backfill


def _calendar_entry(event_id="1", label="Some Championship"):
    return {"id": event_id, "label": label}


def _scoreboard(calendar):
    return {"leagues": [{"calendar": calendar}]}


def _leaderboard(event_id="1", scoring_system="Medal", tournament_name="Some Championship", competitors=None, status_name="STATUS_FINAL", completed=True):
    """Defaults to a real Medal (stroke-play) tournament shape, with one
    placeholder competitor -- callers testing the empty-competitor-data
    gap (see TestProcessTournament's own tests) pass competitors=[]
    instead. Callers testing team stroke play pass
    scoring_system="Teamstroke" (Zurich Classic) -- same flat
    `competitions` shape, still processed normally. Match-scored events
    (Ryder Cup/Presidents Cup/WGC Match Play/The Match) use a genuinely
    different NESTED `competitions` shape -- see _match_leaderboard."""
    if competitors is None:
        competitors = [{"athlete": {"id": "1"}}]
    return {"events": [{
        "id": event_id,
        "date": "2026-08-20T04:00Z",
        "tournament": {"displayName": tournament_name, "scoringSystem": {"name": scoring_system}},
        "status": {"type": {"name": status_name, "completed": completed}},
        "competitions": [{"competitors": competitors}],
    }]}


def _match_leaderboard(event_id="401465497", tournament_name="Presidents Cup", include_cup_summary=True, match_sessions=None):
    """A team-match-play (Ryder Cup/Presidents Cup) or individual-match-
    play (WGC Match Play, include_cup_summary=False) leaderboard -- the
    NESTED `[[...], [...]]` shape confirmed live 2026-08-26 (see
    library.normalize.pga_matchplay's own module docstring). Defaults to
    one Cup-summary entry plus one real foursomes match, both processed
    into DynamoDB."""
    sessions = []
    if include_cup_summary:
        sessions.append([{
            "id": "10950", "description": "tournament", "type": {"text": "tournament"},
            "scoringSystem": {"name": "Cup"},
            "competitors": [
                {"id": "1", "homeAway": "home", "score": {"value": 17.5, "winner": True}, "team": {"id": "1", "displayName": "USA"}},
                {"id": "3", "homeAway": "away", "score": {"value": 12.5, "winner": False}, "team": {"id": "3", "displayName": "INTL"}},
            ],
        }])
    if match_sessions is None:
        match_sessions = [[{
            "id": "10951", "date": "2022-09-22T17:05Z", "description": "Thursday Foursomes",
            "type": {"text": "foursome"}, "scoringSystem": {"name": "Match"},
            "status": {"type": {"name": "STATUS_FINAL", "completed": True}},
            "competitors": [
                {
                    "id": "1085", "homeAway": "home", "score": {"value": 6.0, "displayValue": "6 & 5", "winner": True},
                    "team": {"id": "1", "displayName": "USA"},
                    "roster": [{"athlete": {"id": "1085", "displayName": "Tony Finau"}}, {"athlete": {"id": "1086", "displayName": "Max Homa"}}],
                },
                {
                    "id": "2001", "homeAway": "away", "score": {"value": 0.0, "displayValue": "", "winner": False},
                    "team": {"id": "3", "displayName": "INTL"},
                    "roster": [{"athlete": {"id": "2001", "displayName": "Hideki Matsuyama"}}, {"athlete": {"id": "2002", "displayName": "Sungjae Im"}}],
                },
            ],
        }]]
    sessions.extend(match_sessions)
    return {"events": [{
        "id": event_id,
        "date": "2022-09-22T17:05Z",
        "season": {"year": 2023},
        "tournament": {"displayName": tournament_name, "scoringSystem": {"name": "Match"}},
        "status": {"type": {"name": "STATUS_FINAL", "completed": True}},
        "competitions": sessions,
    }]}


class TestChunkSeasons:
    def test_splits_into_batches_of_the_given_size(self):
        assert backfill.chunk_seasons(2017, 2022, 3) == [[2017, 2018, 2019], [2020, 2021, 2022]]

    def test_uneven_final_batch_is_shorter(self):
        assert backfill.chunk_seasons(2017, 2020, 3) == [[2017, 2018, 2019], [2020]]

    def test_single_season_range(self):
        assert backfill.chunk_seasons(2026, 2026, 3) == [[2026]]


class TestSeasonCalendar:
    def test_queries_june_1_of_the_season_label_year(self):
        client = MagicMock()
        client.get_scoreboard_for_date.return_value = _scoreboard([])

        backfill.season_calendar(client, 2026)

        client.get_scoreboard_for_date.assert_called_once_with("20260601")

    def test_returns_the_calendar_list_and_the_raw_scoreboard(self):
        client = MagicMock()
        calendar = [_calendar_entry("1"), _calendar_entry("2")]
        scoreboard = _scoreboard(calendar)
        client.get_scoreboard_for_date.return_value = scoreboard

        returned_calendar, returned_scoreboard = backfill.season_calendar(client, 2026)

        assert returned_calendar == calendar
        assert returned_scoreboard == scoreboard

    def test_missing_calendar_returns_an_empty_list_not_an_error(self):
        client = MagicMock()
        client.get_scoreboard_for_date.return_value = {"leagues": [{}]}

        calendar, _ = backfill.season_calendar(client, 2026)

        assert calendar == []


class TestProcessTournament:
    def test_skips_fetch_when_leaderboard_already_exists(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = True

        result = backfill.process_tournament(client, storage, 2026, "401811963")

        client.get_leaderboard.assert_not_called()
        assert result == "skipped"

    def test_fetches_writes_and_upserts_when_missing(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = False
        client.get_leaderboard.return_value = _leaderboard("401811963")

        with patch.object(backfill.normalize, "leaderboard_event_to_player_entities", return_value=[{"entity_id": "1"}, {"entity_id": "2"}]), \
             patch.object(backfill.normalize, "leaderboard_event_to_event_item", return_value={"event_id": "401811963"}):
            result = backfill.process_tournament(client, storage, 2026, "401811963")

        storage.put_raw_json.assert_called_once_with("pga/leaderboard/2026/401811963.json", client.get_leaderboard.return_value)
        assert storage.upsert_entity.call_count == 2
        storage.upsert_event.assert_called_once_with({"event_id": "401811963"})
        assert result == "processed"

    def test_no_events_in_response_is_skipped_without_raising(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = False
        client.get_leaderboard.return_value = {"events": []}

        result = backfill.process_tournament(client, storage, 2026, "401811963")  # must not raise

        storage.upsert_event.assert_not_called()
        assert result == "skipped"

    def test_the_match_exhibition_is_skipped_not_normalized(self):
        # The Match -- team+roster shape identical to Ryder Cup's, but no
        # Cup-level summary entry and no guarantee its "athletes" are
        # even PGA Tour golfers (see library.normalize.pga_matchplay.
        # is_exhibition's own docstring, a real 2022 NFL-quarterback
        # edition). Excluded permanently, not deferred.
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = False
        client.get_leaderboard.return_value = _match_leaderboard("401430881", tournament_name="The Match", include_cup_summary=False)

        result = backfill.process_tournament(client, storage, 2026, "401430881")

        storage.upsert_event.assert_not_called()
        storage.upsert_entity.assert_not_called()
        assert result == "skipped"
        # Raw JSON is still preserved even though it's not normalized.
        storage.put_raw_json.assert_called_once()

    def test_unrecognized_scoring_system_is_skipped_not_normalized(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = False
        client.get_leaderboard.return_value = _leaderboard("401219595", scoring_system="Stableford", tournament_name="Barracuda Championship")

        result = backfill.process_tournament(client, storage, 2026, "401219595")

        storage.upsert_event.assert_not_called()
        storage.upsert_entity.assert_not_called()
        assert result == "skipped"

    def test_team_stroke_play_tournament_is_processed(self):
        # Zurich Classic of New Orleans -- Teamstroke is a SUPPORTED
        # flat-stroke-play format (see library.normalize.pga.
        # is_flat_stroke_play), unlike Match-scored team/individual match
        # play.
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = False
        client.get_leaderboard.return_value = _leaderboard("401353230", scoring_system="Teamstroke", tournament_name="Zurich Classic of New Orleans")

        result = backfill.process_tournament(client, storage, 2026, "401353230")

        assert result == "processed"
        storage.upsert_event.assert_called_once()

    def test_team_match_play_tournament_is_processed(self):
        # Ryder Cup / Presidents Cup.
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = False
        client.get_leaderboard.return_value = _match_leaderboard()

        result = backfill.process_tournament(client, storage, 2023, "401465497")

        assert result == "processed"
        # 1 cup-level event + 1 individual match event.
        assert storage.upsert_event.call_count == 2
        event_types = {call.args[0]["event_type"] for call in storage.upsert_event.call_args_list}
        assert event_types == {"cup", "match_play"}
        # 2 national team entities + 4 golfer entities (2 per side).
        assert storage.upsert_entity.call_count == 6

    def test_individual_match_play_tournament_is_processed_with_no_cup_row(self):
        # WGC-Dell Technologies Match Play -- no team layer, no Cup
        # summary, so no "cup" event row, only "match_play" ones.
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = False
        client.get_leaderboard.return_value = _match_leaderboard(
            "401353293", tournament_name="WGC-Dell Technologies Match Play", include_cup_summary=False,
            match_sessions=[[{
                "id": "1", "date": "2022-03-23T07:00Z", "description": "Wednesday Group Play",
                "type": {"text": "singles"}, "scoringSystem": {"name": "Match"},
                "status": {"type": {"name": "STATUS_FINAL", "completed": True}},
                "competitors": [
                    {"id": "3439", "homeAway": "home", "score": {"value": 3.0, "displayValue": "3 & 2", "winner": True}, "athlete": {"id": "3439", "displayName": "Scottie Scheffler"}},
                    {"id": "3448", "homeAway": "away", "score": {"value": 0.0, "displayValue": "", "winner": False}, "athlete": {"id": "3448", "displayName": "Cameron Young"}},
                ],
            }]],
        )

        result = backfill.process_tournament(client, storage, 2022, "401353293")

        assert result == "processed"
        assert storage.upsert_event.call_count == 1
        assert storage.upsert_event.call_args[0][0]["event_type"] == "match_play"
        # 2 golfer entities, no team entities (WGC has no team layer).
        assert storage.upsert_entity.call_count == 2

    def test_match_play_event_with_no_match_data_is_treated_as_a_gap(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = False
        client.get_leaderboard.return_value = _match_leaderboard(match_sessions=[])

        result = backfill.process_tournament(client, storage, 2023, "401465497")

        assert result == "empty"
        storage.upsert_event.assert_not_called()
        storage.upsert_entity.assert_not_called()

    def test_medal_event_with_no_competitor_data_is_treated_as_a_gap_not_processed(self):
        # Real, confirmed ESPN gap (Shriners Hospitals for Children Open /
        # Sanderson Farms Championship / Corales Puntacana Championship,
        # all Fall-2020 events, live-swept 2026-08-26) -- a completed,
        # Medal-scoring event whose own leaderboard has no competitor
        # data at all. Must not be written (would corrupt the cutline
        # dataset's field_size feature to 0 for a real full field).
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = False
        client.get_leaderboard.return_value = _leaderboard("401219795", competitors=[], status_name="STATUS_FINAL")

        result = backfill.process_tournament(client, storage, 2021, "401219795")

        storage.upsert_event.assert_not_called()
        storage.upsert_entity.assert_not_called()
        assert result == "empty"
        # Raw JSON is still preserved even though it's not normalized.
        storage.put_raw_json.assert_called_once()

    def test_canceled_medal_event_with_no_competitor_data_is_also_treated_as_a_gap(self):
        # The 2020 COVID-canceled majority case (THE PLAYERS, The Open,
        # etc.) -- same empty-competitors shape as the completed-event
        # gap above, just with a genuine "nothing was ever played" cause
        # rather than an ESPN data-population miss. Handled identically.
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = False
        client.get_leaderboard.return_value = _leaderboard("401155428", competitors=[], status_name="STATUS_CANCELED")

        result = backfill.process_tournament(client, storage, 2020, "401155428")

        storage.upsert_event.assert_not_called()
        assert result == "empty"

    def test_missing_tournament_metadata_is_skipped_not_normalized(self):
        # A just-added future calendar entry ESPN hasn't fully populated
        # yet -- confirmed live (a real not-yet-configured Presidents Cup
        # entry, 2026-08-25). Fails closed rather than assume Medal.
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = False
        client.get_leaderboard.return_value = {"events": [{"id": "401824815"}]}  # no "tournament" key at all

        result = backfill.process_tournament(client, storage, 2026, "401824815")

        storage.upsert_event.assert_not_called()
        assert result == "skipped"


class TestProcessSeason:
    def test_writes_the_scoreboard_and_counts_skipped_entries(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.return_value = True  # isolate this test to the calendar walk
        calendar = [_calendar_entry("1"), _calendar_entry("2")]

        with patch.object(backfill, "season_calendar", return_value=(calendar, {"leagues": []})):
            result = backfill.process_season(client, storage, 2026)

        assert result["tournaments_processed"] == 0
        assert result["tournaments_skipped"] == 2
        assert result["tournaments_failed"] == 0
        storage.put_raw_json.assert_any_call("pga/scoreboard/20260601.json", {"leagues": []})

    def test_counts_a_real_processed_tournament_separately_from_a_skipped_one(self):
        client = MagicMock()
        storage = MagicMock()
        calendar = [_calendar_entry("1"), _calendar_entry("2")]

        with patch.object(backfill, "season_calendar", return_value=(calendar, {})), \
             patch.object(backfill, "process_tournament", side_effect=["processed", "skipped"]):
            result = backfill.process_season(client, storage, 2026)

        assert result["tournaments_processed"] == 1
        assert result["tournaments_skipped"] == 1
        assert result["tournaments_failed"] == 0

    def test_one_tournament_failure_does_not_block_the_others(self):
        client = MagicMock()
        storage = MagicMock()
        storage.raw_object_exists.side_effect = [False, RuntimeError("boom")]
        calendar = [_calendar_entry("1"), _calendar_entry("2")]

        with patch.object(backfill, "season_calendar", return_value=(calendar, {})), \
             patch.object(backfill, "process_tournament", side_effect=["processed", Exception("ESPN timeout")]):
            result = backfill.process_season(client, storage, 2026)

        assert result["tournaments_processed"] == 1
        assert result["tournaments_failed"] == 1
        assert len(result["failures"]) == 1

    def test_counts_a_gap_tournament_separately_and_records_it(self):
        client = MagicMock()
        storage = MagicMock()
        calendar = [_calendar_entry("1", "Processed Event"), _calendar_entry("2", "Gap Event")]

        with patch.object(backfill, "season_calendar", return_value=(calendar, {})), \
             patch.object(backfill, "process_tournament", side_effect=["processed", "empty"]):
            result = backfill.process_season(client, storage, 2026)

        assert result["tournaments_processed"] == 1
        assert result["tournaments_skipped"] == 0
        assert result["tournaments_empty"] == 1
        assert result["empty_events"] == [{"season": 2026, "event_id": "2", "label": "Gap Event"}]
