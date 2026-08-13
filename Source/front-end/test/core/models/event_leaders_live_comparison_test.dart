import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/event_leaders.dart';

void main() {
  test('toLiveComparison pairs each predicted stat line with its live actual by entity_id', () {
    const leaders = EventLeaders(
      home: TeamLeaders(
        passing: PlayerStatLine(entityId: '100', name: 'QB One', stats: {'passing_yards': 250}),
        receiving: [PlayerStatLine(entityId: '101', name: 'WR One', stats: {'receiving_yards': 80})],
        rushing: [],
        sacks: [],
      ),
      away: TeamLeaders(passing: null, receiving: [], rushing: [], sacks: []),
    );

    final comparison = leaders.toLiveComparison({
      '100': {'passing_yards': 140},
      // '101' deliberately absent -- no live stat line for this player yet.
    });

    expect(comparison.home.passing!.predicted, {'passing_yards': 250.0});
    expect(comparison.home.passing!.actual, {'passing_yards': 140.0});
    expect(comparison.home.receiving.single.actual, isEmpty);
    expect(comparison.away.passing, isNull);
  });
}
