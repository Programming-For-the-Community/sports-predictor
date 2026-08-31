import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/f1_event.dart';

void main() {
  test('parses a full field (main race) event', () {
    final event = F1Event.fromJson({
      'event_id': '2026-5', 'event_type': 'field', 'event_date': '2026-05-24', 'status': 'scheduled',
      'season': 2026, 'week': 5, 'race_name': 'Monaco Grand Prix', 'circuit_id': 'monaco',
      'participants': [
        {
          'entity_id': 'max_verstappen', 'name': 'Max Verstappen', 'constructor_entity_id': 'red_bull',
          'result': {
            'finish_position': 1, 'grid_position': 1, 'status': 'finished', 'points': 25.0, 'fastest_lap': true,
            'laps_completed': 78, 'qualifying': {'position': 1, 'gap_to_pole_seconds': 0.0},
          },
        },
      ],
      'venue_name': 'Circuit de Monaco', 'venue_city': 'Monte Carlo', 'venue_state': 'Monaco',
    });

    expect(event.eventId, '2026-5');
    expect(event.eventType, 'field');
    expect(event.isSprint, isFalse);
    expect(event.raceName, 'Monaco Grand Prix');
    expect(event.circuitId, 'monaco');
    expect(event.venueLabel, 'Circuit de Monaco -- Monte Carlo, Monaco');

    final driver = event.participants.single;
    expect(driver.entityId, 'max_verstappen');
    expect(driver.constructorEntityId, 'red_bull');
    expect(driver.result!.finishPosition, 1);
    expect(driver.result!.fastestLap, isTrue);
    expect(driver.result!.qualifying!.position, 1);
  });

  test('isSprint is true for a sprint event', () {
    final event = F1Event.fromJson({
      'event_id': '2026-5-sprint', 'event_type': 'sprint', 'event_date': '2026-05-23', 'status': 'scheduled',
      'participants': [],
    });
    expect(event.isSprint, isTrue);
  });

  test('a scheduled stub event (no participants, no qualifying yet) parses cleanly', () {
    final event = F1Event.fromJson({
      'event_id': '2026-9', 'event_type': 'field', 'event_date': '2026-06-14', 'status': 'scheduled',
      'circuit_id': 'canada', 'participants': [],
    });

    expect(event.status, 'scheduled');
    expect(event.participants, isEmpty);
  });

  test('a participant with no result parses to a null result, not a crash', () {
    final event = F1Event.fromJson({
      'event_id': '2026-9', 'event_type': 'field', 'event_date': '2026-06-14', 'status': 'scheduled',
      'participants': [
        {'entity_id': 'max_verstappen', 'constructor_entity_id': 'red_bull'},
      ],
    });

    expect(event.participants.single.result, isNull);
  });

  test('venueLabel is null when no venue fields exist', () {
    final event = F1Event.fromJson({
      'event_id': '2026-9', 'event_type': 'field', 'event_date': '2026-06-14', 'status': 'scheduled', 'participants': [],
    });
    expect(event.venueLabel, isNull);
  });
}
