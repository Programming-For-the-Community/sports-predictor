"""
Unit tests for aws-lambdas/ncaafb/ingest/enrichment.py -- enrich_games'
overall best-effort wiring only. The coach/rank school-name lookup
builders and coach cache it delegates to are covered by
tests/library/storage/test_ncaafb_coach_cache.py; venue_indoor itself is
covered by tests/library/storage/test_ncaafb_team_cache.py.

The ncaafb_ingest module (which pulls in its sibling enrichment.py via a
plain sys.path entry) is registered in sys.modules by conftest.py.
"""
from unittest.mock import MagicMock, patch

from enrichment import enrich_games


def _coach(first, last, school, year, wins, losses, ties=0, hire_year=None):
    return {
        "firstName": first, "lastName": last,
        "hireDate": f"{hire_year}-01-15" if hire_year else None,
        "seasons": [{"school": school, "year": year, "wins": wins, "losses": losses, "ties": ties}],
    }


class TestEnrichGames:
    def _teams(self):
        return [
            {"id": "2", "school": "Georgia", "conference": "SEC", "location": {"dome": False}},
            {"id": "52", "school": "Alabama", "conference": "SEC", "location": {"dome": False}},
        ]

    def test_attaches_coach_and_rank_by_resolving_school_via_teams(self):
        games = [{"id": "1", "homeId": "2", "awayId": "52"}]
        mock_client = MagicMock()
        mock_client.get_rankings.return_value = [{"polls": [
            {"poll": "AP Top 25", "ranks": [{"school": "Georgia", "rank": 1}]},
        ]}]
        mock_s3 = MagicMock()

        with patch("enrichment.attach_venue_indoor"), \
             patch("enrichment.get_cached_teams", return_value=self._teams()), \
             patch("enrichment.get_cached_coaches", return_value=[
                 _coach("Kirby", "Smart", "Georgia", 2025, 11, 1, hire_year=2016),
             ]):
            enrich_games(games, 2025, 4, mock_client, mock_s3, "bucket")

        assert games[0]["home_coach"]["coach_name"] == "Kirby Smart"
        assert games[0]["home_current_rank"] == 1
        assert games[0]["away_coach"] is None
        assert games[0]["away_current_rank"] is None

    def test_coach_fetch_failure_omits_coach_fields_without_raising(self):
        games = [{"id": "1", "homeId": "2", "awayId": "52"}]
        mock_client = MagicMock()
        mock_client.get_rankings.return_value = []
        mock_s3 = MagicMock()

        with patch("enrichment.attach_venue_indoor"), \
             patch("enrichment.get_cached_teams", return_value=self._teams()), \
             patch("enrichment.get_cached_coaches", side_effect=Exception("CFBD timeout")):
            enrich_games(games, 2025, 4, mock_client, mock_s3, "bucket")  # must not raise

        assert games[0]["home_coach"] is None
        assert games[0]["away_coach"] is None

    def test_rankings_fetch_failure_omits_rank_fields_without_raising(self):
        games = [{"id": "1", "homeId": "2", "awayId": "52"}]
        mock_client = MagicMock()
        mock_client.get_rankings.side_effect = Exception("CFBD timeout")
        mock_s3 = MagicMock()

        with patch("enrichment.attach_venue_indoor"), \
             patch("enrichment.get_cached_teams", return_value=self._teams()), \
             patch("enrichment.get_cached_coaches", return_value=[]):
            enrich_games(games, 2025, 4, mock_client, mock_s3, "bucket")  # must not raise

        assert games[0]["home_current_rank"] is None
        assert games[0]["away_current_rank"] is None

    def test_rankings_scoped_to_the_given_week(self):
        games = [{"id": "1", "homeId": "2", "awayId": "52"}]
        mock_client = MagicMock()
        mock_client.get_rankings.return_value = []
        mock_s3 = MagicMock()

        with patch("enrichment.attach_venue_indoor"), \
             patch("enrichment.get_cached_teams", return_value=self._teams()), \
             patch("enrichment.get_cached_coaches", return_value=[]):
            enrich_games(games, 2025, 7, mock_client, mock_s3, "bucket")

        mock_client.get_rankings.assert_called_once_with(2025, week=7)

    def test_venue_indoor_fetch_failure_does_not_block_coach_rank_enrichment(self):
        games = [{"id": "1", "homeId": "2", "awayId": "52"}]
        mock_client = MagicMock()
        mock_client.get_rankings.return_value = [{"polls": [
            {"poll": "AP Top 25", "ranks": [{"school": "Georgia", "rank": 1}]},
        ]}]
        mock_s3 = MagicMock()

        with patch("enrichment.attach_venue_indoor", side_effect=Exception("CFBD timeout")), \
             patch("enrichment.get_cached_teams", return_value=self._teams()), \
             patch("enrichment.get_cached_coaches", return_value=[
                 _coach("Kirby", "Smart", "Georgia", 2025, 11, 1, hire_year=2016),
             ]):
            enrich_games(games, 2025, 4, mock_client, mock_s3, "bucket")  # must not raise

        assert games[0]["home_current_rank"] == 1
