import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/f1_event.dart';
import 'package:front_end/core/models/f1_live_score.dart';
import 'package:front_end/core/widgets/f1_event_row.dart';

F1Event _scheduledEvent() => const F1Event(
      eventId: '2026-16',
      eventType: 'field',
      eventDate: '2026-09-06',
      status: 'scheduled',
      participants: [],
      raceName: 'Italian Grand Prix',
    );

void main() {
  group('F1EventRow', () {
    testWidgets('a scheduled race with no live state shows UPCOMING', (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: F1EventRow(sport: 'f1', event: _scheduledEvent())),
      ));

      expect(find.text('UPCOMING'), findsOneWidget);
      expect(find.text('FINAL'), findsNothing);
    });

    testWidgets(
        'a race ESPN reports as over shows FINAL, even though event.status is still "scheduled" '
        '(ingest hasn\'t caught up with the real result yet)', (tester) async {
      // Regression: previously this row only ever looked at event.status,
      // which can lag the real result by up to ~24h -- a just-finished
      // race looked identical to one that hadn't happened yet.
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: F1EventRow(
            sport: 'f1',
            event: _scheduledEvent(),
            liveState: const F1LiveEventState(eventType: 'field', state: 'post'),
          ),
        ),
      ));

      expect(find.text('FINAL'), findsOneWidget);
      expect(find.text('UPCOMING'), findsNothing);
    });

    testWidgets('a race currently in progress shows LIVE, not UPCOMING or FINAL', (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: F1EventRow(
            sport: 'f1',
            event: _scheduledEvent(),
            liveState: const F1LiveEventState(eventType: 'field', state: 'in'),
          ),
        ),
      ));

      expect(find.text('LIVE'), findsOneWidget);
      expect(find.text('UPCOMING'), findsNothing);
      expect(find.text('FINAL'), findsNothing);
    });
  });
}