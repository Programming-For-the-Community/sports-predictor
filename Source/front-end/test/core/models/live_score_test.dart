import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/live_score.dart';

void main() {
  test('parses a live event', () {
    final state = LiveEventState.fromJson({
      'live': true,
      'detail': 'Q3 08:14',
      'home_score': 17,
      'away_score': 14,
    });

    expect(state.live, isTrue);
    expect(state.detail, 'Q3 08:14');
    expect(state.homeScore, 17.0);
    expect(state.awayScore, 14.0);
  });

  test('a not-yet-kicked-off event in the poll window is live=false with no detail', () {
    final state = LiveEventState.fromJson({
      'live': false,
      'detail': null,
      'home_score': null,
      'away_score': null,
    });

    expect(state.live, isFalse);
    expect(state.detail, isNull);
    expect(state.homeScore, isNull);
  });
}
