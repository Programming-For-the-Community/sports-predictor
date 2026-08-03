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
          'projected_wins': 13.4,
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
}
