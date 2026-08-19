import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/season_projection.dart';

void main() {
  test('parses standings and leaderboards from a full response', () {
    final season = SeasonProjection.fromJson({
      'sport': 'nfl',
      'season': 2026,
      'standings': [
        {
          'team_id': '12',
          'wins': 8,
          'losses': 2,
          'ties': 0,
          'projected_wins': 13.4,
          'projected_losses': 3.6,
          'division_winner_probability': 0.91,
          'playoff_probability': 0.98,
          'championship_probability': 0.22,
        },
      ],
      'leaderboards': {
        'passing_yards': [
          {'entity_id': '3139477', 'name': 'Patrick Mahomes', 'current_total': 2100.0, 'projected_total': 4300.0},
        ],
      },
      'generated_at': '2026-08-03T00:00:00Z',
    });

    expect(season.season, 2026);
    expect(season.standings.single.teamId, '12');
    expect(season.standings.single.projectedWins, 13.4);
    expect(season.leaderboards!['passing_yards']!.single.displayName, 'Patrick Mahomes');
  });

  test('ties/projected_losses default rather than throw when a stale payload omits them', () {
    // Real scenario, not hypothetical -- the season projection is a
    // weekly-precomputed S3 payload, so a frontend deploy can land
    // before the backend has next run with these two fields present.
    final standing = TeamStanding.fromJson({
      'team_id': '12',
      'wins': 8,
      'losses': 2,
      'projected_wins': 13.4,
      'division_winner_probability': 0.91,
      'playoff_probability': 0.98,
      'championship_probability': 0.22,
    });

    expect(standing.ties, 0);
    expect(standing.projectedLosses, 0.0);
  });

  test('a standing with no simulation fields yet defaults instead of throwing', () {
    // Real scenario for NCAAFB specifically -- build_season_projection
    // skips simulation entirely (fewer than CFP_FIELD_SIZE teams tracked,
    // or no promoted ranking model yet) but still writes wins/losses/
    // conference for every team. See TeamStanding.fromJson's own doc
    // comment.
    final standing = TeamStanding.fromJson({'team_id': '333', 'conference': 'SEC', 'wins': 3, 'losses': 1});

    expect(standing.division, 'SEC');
    expect(standing.projectedWins, 3.0);
    expect(standing.projectedLosses, 0.0);
    expect(standing.divisionWinnerProbability, 0.0);
    expect(standing.playoffProbability, 0.0);
    expect(standing.championshipProbability, 0.0);
  });

  test('conference_champion_probability (NCAAFB) is read the same as division_winner_probability (NFL)', () {
    final standing = TeamStanding.fromJson({
      'team_id': '333', 'conference': 'SEC', 'wins': 8, 'losses': 1,
      'projected_wins': 10.2, 'conference_champion_probability': 0.35,
    });

    expect(standing.divisionWinnerProbability, 0.35);
  });

  test('leaderboards is null when the backend could not compute it', () {
    final season = SeasonProjection.fromJson({
      'sport': 'nfl',
      'season': 2026,
      'standings': [],
      'leaderboards': null,
      'generated_at': '2026-08-03T00:00:00Z',
    });

    expect(season.leaderboards, isNull);
    expect(season.standings, isEmpty);
  });

  test('leaderboard entry falls back to entity_id when name is missing', () {
    final entry = LeaderboardEntry.fromJson({
      'entity_id': '3139477',
      'current_total': 100.0,
      'projected_total': 200.0,
    });

    expect(entry.displayName, '3139477');
  });

  test('a bracket matchup with wins_a/wins_b parses as a series', () {
    final matchup = BracketMatchup.fromJson({
      'team_a': '2', 'team_b': '8', 'seed_a': 1, 'seed_b': 8, 'status': 'scheduled',
      'predicted_winner': '2', 'win_probability': 0.81, 'wins_a': 2, 'wins_b': 1,
    });

    expect(matchup.isSeries, isTrue);
    expect(matchup.winsA, 2);
    expect(matchup.winsB, 1);
  });

  test('a bracket matchup with no wins_a/wins_b is not a series (NFL/NCAAFB, NBA Play-In)', () {
    final matchup = BracketMatchup.fromJson({
      'team_a': '12', 'team_b': '13', 'status': 'projected',
      'predicted_winner': '12', 'win_probability': 0.6,
    });

    expect(matchup.isSeries, isFalse);
    expect(matchup.winsA, isNull);
    expect(matchup.winsB, isNull);
  });

  test('a bracket matchup with no status field defaults to projected instead of crashing', () {
    // Regression for a real production crash (2026-08-19): a real
    // payload's cup_bracket matchups came from the backend with no
    // "status" key at all, and the previous `json['status'] as String`
    // (non-nullable, no fallback) threw on the missing key.
    final matchup = BracketMatchup.fromJson({
      'team_a': '2', 'team_b': '8', 'predicted_winner': '2', 'win_probability': 0.6,
    });

    expect(matchup.status, 'projected');
  });
}
