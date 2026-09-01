import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/f1_live_score.dart';

void main() {
  test('parses an event-level live state with per-driver order/winner keyed by entity_id', () {
    final state = F1LiveEventState.fromJson({
      'event_type': 'field',
      'status': 'In Progress',
      'state': 'in',
      'race_name': 'Pirelli Italian Grand Prix',
      'participants': {
        'max_verstappen': {'order': 1, 'winner': false},
        'lando_norris': {'order': 2, 'winner': false},
      },
    });

    expect(state.eventType, 'field');
    expect(state.status, 'In Progress');
    expect(state.state, 'in');
    expect(state.raceName, 'Pirelli Italian Grand Prix');
    expect(state.isLive, isTrue);
    expect(state.participants['max_verstappen']!.order, 1);
    expect(state.participants['lando_norris']!.order, 2);
  });

  test('defaults to an empty participants map when absent', () {
    final state = F1LiveEventState.fromJson({'event_type': 'field', 'state': 'pre'});

    expect(state.participants, isEmpty);
    expect(state.isLive, isFalse);
  });

  test('isLive is false once ESPN flips state to post, even with participants still cached (end-buffer tail)', () {
    final state = F1LiveEventState.fromJson({
      'event_type': 'field',
      'state': 'post',
      'participants': {
        'max_verstappen': {'order': 1, 'winner': true},
      },
    });

    expect(state.isLive, isFalse);
    expect(state.participants['max_verstappen']!.winner, isTrue);
  });

  test('winner defaults to false and order to null when a competitor has neither yet', () {
    final result = F1DriverLiveResult.fromJson({});

    expect(result.order, isNull);
    expect(result.winner, isFalse);
  });
}
