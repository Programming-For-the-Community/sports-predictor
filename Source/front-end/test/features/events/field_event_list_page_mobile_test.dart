import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/field_events_repository.dart';
import 'package:front_end/core/models/field_event.dart';
import 'package:front_end/features/events/field_event_list_page.dart';

import '../../support/mobile_viewport.dart';

FieldEvent _scheduledEvent(String id, String eventDate, String endDate) => FieldEvent(
      eventId: id,
      eventType: 'field',
      eventDate: eventDate,
      endDate: endDate,
      status: 'scheduled',
      season: 2026,
      tournamentName: 'BMW Championship',
      participants: const [FieldParticipant(entityId: '10140', name: 'Xander Schauffele')],
      venueName: 'Bellerive Country Club',
      venueCity: 'St. Louis',
      venueState: 'MO',
    );

void main() {
  for (final width in mobileViewportWidths) {
    testWidgets('renders with no overflow at ${width}px wide', (tester) async {
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [
            fieldEventsListProvider.overrideWith(
              (ref, query) async => query.status == 'completed'
                  ? [_scheduledEvent('401811960', '2026-08-13', '2026-08-16')]
                  : [
                      _scheduledEvent('401811963', '2026-08-20', '2026-08-23'),
                      _scheduledEvent('401811964', '2026-08-27', '2026-08-30'),
                    ],
            ),
          ],
          child: const MaterialApp(home: Scaffold(body: FieldEventListPage(sportId: 'pga'))),
        ),
      );
      expect(tester.takeException(), isNull);

      await tester.tap(find.text('Completed'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    });
  }
}
