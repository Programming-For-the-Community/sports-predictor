import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/event.dart';

void main() {
  test('parses home/away participants from an event', () {
    final event = SportEvent.fromJson({
      'event_id': '401547417',
      'event_date': '2025-09-28',
      'status': 'scheduled',
      'week': 4,
      'participants': [
        {'entity_id': 'KC', 'role': 'home'},
        {'entity_id': 'LAC', 'role': 'away'},
      ],
    });

    expect(event.eventId, '401547417');
    expect(event.home.entityId, 'KC');
    expect(event.away.entityId, 'LAC');
  });
}
