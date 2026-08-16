import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/event_leaders.dart';

void main() {
  test('toLiveComparison pairs each predicted stat line with its live actual by entity_id', () {
    const leaders = EventLeaders(
      home: TeamLeaders({
        'passing': [PlayerStatLine(entityId: '100', name: 'QB One', stats: {'passing_yards': 250})],
        'receiving': [PlayerStatLine(entityId: '101', name: 'WR One', stats: {'receiving_yards': 80})],
        'rushing': [],
        'sacks': [],
      }),
      away: TeamLeaders({'passing': [], 'receiving': [], 'rushing': [], 'sacks': []}),
    );

    final comparison = leaders.toLiveComparison({
      '100': {'passing_yards': 140},
      // '101' deliberately absent -- no live stat line for this player yet.
    });

    expect(comparison.home['passing'].single.predicted, {'passing_yards': 250.0});
    expect(comparison.home['passing'].single.actual, {'passing_yards': 140.0});
    expect(comparison.home['receiving'].single.actual, isEmpty);
    expect(comparison.away['passing'], isEmpty);
  });

  test('fromJson normalizes a singular category (NFL/NCAAFB passing) into a 0-or-1-element list', () {
    final team = TeamLeaders.fromJson({
      'passing': {'entity_id': '100', 'name': 'QB One', 'passing_yards': 250},
      'receiving': null,
    });

    expect(team['passing'].single.entityId, '100');
    expect(team['receiving'], isEmpty);
  });

  test('fromJson leaves an already-list category (NBA/NCAA MBB) untouched', () {
    final team = TeamLeaders.fromJson({
      'scoring': [
        {'entity_id': '1', 'name': 'Player One', 'points': 27},
        {'entity_id': '2', 'name': 'Player Two', 'points': 18},
      ],
    });

    expect(team['scoring'].map((p) => p.entityId), ['1', '2']);
    expect(team['rebounding'], isEmpty);
  });
}
