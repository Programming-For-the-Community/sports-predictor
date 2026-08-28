import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/field_event.dart';

Map<String, dynamic> _baseEvent() => {
      'event_id': '401811963',
      'event_type': 'field',
      'event_date': '2026-08-20',
      'end_date': '2026-08-23',
      'status': 'scheduled',
      'season': 2026,
      'tournament_name': 'BMW Championship',
      'participants': [
        {'entity_id': '10140', 'name': 'Xander Schauffele'},
      ],
      'venue_name': 'Bellerive Country Club',
      'venue_city': 'St. Louis',
      'venue_state': 'MO',
    };

void main() {
  test('parses the top-level tournament fields', () {
    final event = FieldEvent.fromJson(_baseEvent());

    expect(event.eventId, '401811963');
    expect(event.eventType, 'field');
    expect(event.tournamentName, 'BMW Championship');
    expect(event.endDate, '2026-08-23');
  });

  test('venueLabel joins name and city/state', () {
    final event = FieldEvent.fromJson(_baseEvent());

    expect(event.venueLabel, 'Bellerive Country Club -- St. Louis, MO');
  });

  test('venueLabel is null when nothing is present', () {
    final json = _baseEvent()..['venue_name'] = null..['venue_city'] = null..['venue_state'] = null;

    final event = FieldEvent.fromJson(json);

    expect(event.venueLabel, isNull);
  });

  group('FieldParticipant', () {
    test('parses without crashing on a participant with no role key at all', () {
      // Unlike Participant.fromJson's non-nullable `json['role'] as String`
      // cast, a real PGA participant never carries a role key.
      final participant = FieldParticipant.fromJson({'entity_id': '10140', 'name': 'Xander Schauffele'});

      expect(participant.entityId, '10140');
      expect(participant.name, 'Xander Schauffele');
    });

    test('result is null before any result exists', () {
      final participant = FieldParticipant.fromJson({'entity_id': '10140'});

      expect(participant.result, isNull);
    });

    test('parses a completed result', () {
      final participant = FieldParticipant.fromJson({
        'entity_id': '10140',
        'result': {'finish_position': 26, 'is_tie': true, 'status': 'finished', 'score_to_par': -4, 'total_strokes': 276.0},
      });

      expect(participant.result!.finishPosition, 26);
      expect(participant.result!.isTie, isTrue);
      expect(participant.result!.status, 'finished');
    });
  });
}
