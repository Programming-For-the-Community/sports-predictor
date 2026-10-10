"""
Unit tests for library.features.hockey_live against an in-memory
FeatureStorage stand-in: the starting-goalie resolution order, and that a
live row matches the training row built for the same game from the same
history (the train/serve parity the models depend on). Uses real NHL team
ids (BOS=1, NYR=13).
"""
import logging

import pytest

from library.features import hockey_live, nhl, nhl_dataset
from library.features.live_orchestration import EventNotFoundError

from _nhl_test_helpers import event as _event, goalie as _goalie, skater as _skater, team_box as _team_box

BOS, NYR = "1", "13"
logger = logging.getLogger("test")


class FakeStorage:
    """The FeatureStorage reads hockey_live makes, over plain lists."""

    def __init__(self, completed, upcoming, boxes, players, rosters):
        self.completed = sorted(completed, key=lambda e: e["event_date"], reverse=True)
        self.upcoming = {e["event_key"]: e for e in upcoming}
        self.boxes, self.players, self.rosters = boxes, players, rosters

    def get_event(self, event_key):
        return self.upcoming.get(event_key) or next((e for e in self.completed if e["event_key"] == event_key), None)

    def get_all_events(self, sport, **kwargs):
        return list(self.completed)

    def get_all_team_game_stats(self, sport, since_date=None):
        return list(self.boxes)

    def get_team_events(self, sport, team_id, before_date=None, limit=None, events=None):
        rows = [
            e for e in (events if events is not None else self.completed)
            if any(p["entity_id"] == team_id for p in e["participants"]) and (before_date is None or e["event_date"] < before_date)
        ]
        return rows[:limit] if limit is not None else rows

    def get_player_game_stats(self, entity_id, before_date=None, limit=None):
        rows = sorted(
            (r for r in self.players if r["entity_id"] == entity_id and (before_date is None or r["event_date"] < before_date)),
            key=lambda r: r["event_date"], reverse=True,
        )
        return rows[:limit] if limit is not None else rows

    def get_player_game_stats_for_event(self, event_key):
        return [r for r in self.players if r["event_key"] == event_key]

    def get_team_entities(self, sport, team_id):
        return [e for e in self.rosters.values() if e["metadata"]["team_id"] == team_id]

    def get_entity(self, sport, entity_id, entity_type):
        return self.rosters.get(entity_id)


def _entity(entity_id, team_id, position):
    return {"entity_id": entity_id, "name": f"Player {entity_id}", "metadata": {"team_id": team_id, "position": position}}


def _played(event_id, day, home, away, home_periods, away_periods, *, home_goalie=None, away_goalie=None):
    """A completed game with a box score, one starting goalie and one skater per side."""
    game = _event(event_id, day, home, away, home_periods, away_periods)
    boxes = [_team_box(event_id, home, shots_total=31), _team_box(event_id, away, shots_total=27)]
    players = [
        _goalie(event_id, day, home, home_goalie or f"g-{home}"), _goalie(event_id, day, away, away_goalie or f"g-{away}"),
        _skater(event_id, day, home, f"s-{home}", toi=1100, points=1), _skater(event_id, day, away, f"s-{away}", toi=1000),
    ]
    for row in players:
        if row["position_group"] == "skater":
            row["position"] = "C"
            row["stat_line"].update({"shots_total": 3, "shots_missed": 1, "hits": 2, "blocked_shots": 1})
    return game, boxes, players


def _scheduled(event_id, day, home, away, **fields):
    game = _event(event_id, day, home, away, [0, 0, 0], [0, 0, 0])
    game.update({"status": "scheduled", "went_to_overtime": None, "decided_by_shootout": None, **fields})
    for participant in game["participants"]:
        participant["result"] = {"score": 0, "won": False}
    return game


def _storage(history, upcoming):
    rosters = {}
    for team in (NYR, BOS):
        rosters[f"g-{team}"] = _entity(f"g-{team}", team, "G")
        rosters[f"b-{team}"] = _entity(f"b-{team}", team, "G")
        rosters[f"s-{team}"] = _entity(f"s-{team}", team, "C")
    return FakeStorage(
        [g[0] for g in history], [upcoming], [row for g in history for row in g[1]],
        [row for g in history for row in g[2]], rosters,
    )


HISTORY = [
    _played("1", "2026-01-04", NYR, BOS, [2, 1, 0], [0, 1, 0]),
    _played("2", "2026-01-06", BOS, NYR, [1, 0, 0], [0, 0, 2]),
    _played("3", "2026-01-08", NYR, BOS, [0, 0, 1], [0, 0, 0]),
]


class TestStartingGoalie:
    def _row(self, upcoming, history=HISTORY):
        storage = _storage(history, upcoming)
        return hockey_live.build_live_event_features(storage, "nhl", upcoming["event_key"])

    def test_confirmed_probable_is_used_as_given(self):
        game = _scheduled("9", "2026-01-12", NYR, BOS, home_probable_goalie_id=f"b-{NYR}", home_probable_goalie_status="Confirmed")

        row = self._row(game)

        assert (row["home_goalie_id"], row["home_goalie_source"]) == (f"b-{NYR}", "confirmed")
        assert row["home_goalie_career_starts"] == 0      # the backup has not started yet

    def test_expected_probable_is_flagged_as_probable(self):
        game = _scheduled("9", "2026-01-12", NYR, BOS, away_probable_goalie_id=f"g-{BOS}", away_probable_goalie_status="Expected")

        row = self._row(game)

        assert (row["away_goalie_id"], row["away_goalie_source"]) == (f"g-{BOS}", "probable")
        assert row["away_goalie_career_starts"] == 3

    def test_with_nothing_listed_the_number_one_goalie_is_predicted(self):
        row = self._row(_scheduled("9", "2026-01-12", NYR, BOS))

        assert (row["home_goalie_id"], row["home_goalie_source"]) == (f"g-{NYR}", "predicted")
        assert (row["away_goalie_id"], row["away_goalie_source"]) == (f"g-{BOS}", "predicted")

    def test_the_other_goalie_is_predicted_when_the_number_one_started_yesterday(self):
        history = HISTORY + [_played("4", "2026-01-09", BOS, NYR, [1, 0, 0], [0, 0, 0], away_goalie=f"b-{NYR}")]
        back_to_back = history + [_played("5", "2026-01-11", NYR, BOS, [1, 0, 0], [0, 0, 0])]

        row = self._row(_scheduled("9", "2026-01-12", NYR, BOS), history=back_to_back)

        # g-13 has four starts and played on the 11th; b-13 has one.
        assert (row["home_goalie_id"], row["home_goalie_source"]) == (f"b-{NYR}", "predicted")

    def test_a_team_with_no_goalies_on_file_has_no_goalie_columns(self):
        storage = _storage(HISTORY, _scheduled("9", "2026-01-12", NYR, BOS))
        storage.rosters = {k: v for k, v in storage.rosters.items() if v["metadata"]["position"] != "G"}

        row = hockey_live.build_live_event_features(storage, "nhl", "SPORT#NHL#EVENT#9")

        assert (row["home_goalie_id"], row["home_goalie_source"]) == (None, None)
        assert row["home_goalie_save_pct_career"] is None

    def test_unknown_event_raises_not_found(self):
        storage = _storage(HISTORY, _scheduled("9", "2026-01-12", NYR, BOS))

        with pytest.raises(EventNotFoundError):
            hockey_live.build_live_event_features(storage, "nhl", "SPORT#NHL#EVENT#404")


class TestTrainServeParity:
    """The live row for a game must equal the training row built for it."""

    NEXT = _played("9", "2026-01-12", NYR, BOS, [3, 0, 0], [1, 0, 0])

    def _training(self):
        return nhl_dataset.build_datasets(
            [g[0] for g in HISTORY + [self.NEXT]], [row for g in HISTORY + [self.NEXT] for row in g[1]],
            [row for g in HISTORY + [self.NEXT] for row in g[2]], logger,
        )

    def _upcoming(self):
        return _scheduled(
            "9", "2026-01-12", NYR, BOS,
            home_probable_goalie_id=f"g-{NYR}", home_probable_goalie_status="Confirmed",
            away_probable_goalie_id=f"g-{BOS}", away_probable_goalie_status="Confirmed",
        )

    def test_event_row_matches_training_outside_the_lineup_group(self):
        training_row = self._training()[0][-1]
        live_row = hockey_live.build_live_event_features(_storage(HISTORY, self._upcoming()), "nhl", "SPORT#NHL#EVENT#9")

        compared = [c for c in training_row if nhl.feature_group(c) not in (None, "lineup")]
        assert len(compared) > 250
        mismatched = {c: (training_row[c], live_row.get(c)) for c in compared if training_row[c] != pytest.approx(live_row.get(c))}
        assert mismatched == {}

    def test_lineup_columns_are_never_built_live(self):
        live_row = hockey_live.build_live_event_features(_storage(HISTORY, self._upcoming()), "nhl", "SPORT#NHL#EVENT#9")

        assert live_row["home_ice_time_share_missing"] is None
        assert live_row["label_home_won"] is False     # an unplayed game carries no real label

    def test_skater_row_matches_training(self):
        training_row = next(r for r in self._training()[2] if r["entity_id"] == f"s-{NYR}" and r["event_date"] == "2026-01-12")
        live_row = hockey_live.build_live_player_features(_storage(HISTORY, self._upcoming()), "nhl", "SPORT#NHL#EVENT#9", f"s-{NYR}")

        compared = [c for c in training_row if not c.startswith("label_")]
        mismatched = {c: (training_row[c], live_row.get(c)) for c in compared if training_row[c] != pytest.approx(live_row.get(c))}
        assert mismatched == {}
        assert live_row["label_stat_line"] == {}

    def test_goalie_row_matches_training(self):
        training_row = next(r for r in self._training()[1] if r["entity_id"] == f"g-{BOS}" and r["event_date"] == "2026-01-12")
        live_row = hockey_live.build_live_player_features(_storage(HISTORY, self._upcoming()), "nhl", "SPORT#NHL#EVENT#9", f"g-{BOS}")

        compared = [c for c in training_row if not c.startswith("label_")]
        mismatched = {c: (training_row[c], live_row.get(c)) for c in compared if training_row[c] != pytest.approx(live_row.get(c))}
        assert mismatched == {}

    def test_unknown_player_raises_not_found(self):
        with pytest.raises(EventNotFoundError):
            hockey_live.build_live_player_features(_storage(HISTORY, self._upcoming()), "nhl", "SPORT#NHL#EVENT#9", "nobody")


class TestLeaderCandidates:
    def test_each_team_gets_its_skaters_by_ice_time_and_its_starting_goalie(self):
        storage = _storage(HISTORY, _scheduled("9", "2026-01-12", NYR, BOS))

        candidates = hockey_live.build_live_event_leader_candidates(storage, "nhl", "SPORT#NHL#EVENT#9")

        assert [row["entity_id"] for row in candidates["home"]["skaters"]] == [f"s-{NYR}"]
        assert candidates["home"]["goalie"]["entity_id"] == f"g-{NYR}"
        assert candidates["away"]["goalie"]["entity_id"] == f"g-{BOS}"
        assert candidates["home"]["skaters"][0]["avg_shots_total"] == 3
        assert candidates["home"]["goalie"]["avg_saves"] == 27


class TestLiveElo:
    def test_future_event_uses_current_ratings_and_a_played_one_its_recorded_pre_game_ratings(self):
        completed = [g[0] for g in HISTORY]
        future = _scheduled("9", "2026-01-12", NYR, BOS)

        live = hockey_live.live_elo_ratings(completed, future, NYR, BOS)[future["event_key"]]
        replay = hockey_live.live_elo_ratings(completed, completed[0], NYR, BOS)[completed[0]["event_key"]]

        assert live["home_pre_rating"] > 1500.0 > live["away_pre_rating"]   # the Rangers won all three
        assert replay == {"home_pre_rating": 1500.0, "away_pre_rating": 1500.0, **{k: v for k, v in replay.items() if k.endswith("_points_scored") or k.endswith("_points_allowed")}}
