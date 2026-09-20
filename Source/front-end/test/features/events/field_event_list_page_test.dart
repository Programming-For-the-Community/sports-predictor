import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/field_events_repository.dart';
import 'package:front_end/core/models/field_event.dart';
import 'package:front_end/features/events/field_event_list_page.dart';

FieldEvent _event(String id, String eventDate, {String tournamentName = 'BMW Championship'}) => FieldEvent(
      eventId: id,
      eventType: 'field',
      eventDate: eventDate,
      status: 'scheduled',
      tournamentName: tournamentName,
      participants: const [],
    );

Widget _wrap({required List<FieldEvent> Function(String status) eventsFor}) {
  return ProviderScope(
    overrides: [
      fieldEventsListProvider.overrideWith((ref, query) async => eventsFor(query.status)),
    ],
    child: const MaterialApp(home: Scaffold(body: FieldEventListPage(sportId: 'pga'))),
  );
}

void main() {
  group('FieldEventListPage', () {
    testWidgets('shows Upcoming tournaments sorted soonest-first by default', (tester) async {
      await tester.pumpWidget(_wrap(eventsFor: (status) => status == 'scheduled'
          ? [_event('2', '2026-08-27', tournamentName: 'Tour Championship'), _event('1', '2026-08-20', tournamentName: 'BMW Championship')]
          : []));
      await tester.pumpAndSettle();

      final bmwCenter = tester.getCenter(find.text('BMW Championship'));
      final tourCenter = tester.getCenter(find.text('Tour Championship'));
      expect(bmwCenter.dy, lessThan(tourCenter.dy));
    });

    testWidgets('switching to Completed shows completed tournaments sorted most-recent-first', (tester) async {
      await tester.pumpWidget(_wrap(eventsFor: (status) => status == 'completed'
          ? [_event('1', '2026-08-06', tournamentName: 'St. Jude Championship'), _event('2', '2026-08-13', tournamentName: 'BMW Championship')]
          : []));
      await tester.pumpAndSettle();

      await tester.tap(find.text('Completed'));
      await tester.pumpAndSettle();

      final bmwCenter = tester.getCenter(find.text('BMW Championship'));
      final stJudeCenter = tester.getCenter(find.text('St. Jude Championship'));
      expect(bmwCenter.dy, lessThan(stJudeCenter.dy));
    });

    testWidgets('shows Coming Soon for an empty Upcoming list', (tester) async {
      await tester.pumpWidget(_wrap(eventsFor: (status) => []));
      await tester.pumpAndSettle();

      expect(find.text('Coming Soon'), findsOneWidget);
    });

    testWidgets('shows a not-found message for an empty Completed list', (tester) async {
      await tester.pumpWidget(_wrap(eventsFor: (status) => []));
      await tester.pumpAndSettle();

      await tester.tap(find.text('Completed'));
      await tester.pumpAndSettle();

      expect(find.text('No tournaments found.'), findsOneWidget);
    });

    testWidgets('shows a loading spinner while the list is fetching', (tester) async {
      await tester.pumpWidget(ProviderScope(
        overrides: [
          fieldEventsListProvider.overrideWith((ref, query) => Completer<List<FieldEvent>>().future),
        ],
        child: const MaterialApp(home: Scaffold(body: FieldEventListPage(sportId: 'pga'))),
      ));

      expect(find.byType(CircularProgressIndicator), findsOneWidget);
    });

    testWidgets('shows an error message when the fetch fails', (tester) async {
      await tester.pumpWidget(ProviderScope(
        overrides: [
          fieldEventsListProvider.overrideWith((ref, query) async => throw Exception('network down')),
        ],
        child: const MaterialApp(home: Scaffold(body: FieldEventListPage(sportId: 'pga'))),
      ));
      await tester.pumpAndSettle();

      expect(find.textContaining('Couldn\'t load tournaments'), findsOneWidget);
    });
  });
}
