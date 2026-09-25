import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/f1_events_repository.dart';
import 'package:front_end/core/data/live_scores_repository.dart';
import 'package:front_end/core/models/f1_event.dart';
import 'package:front_end/features/events/f1_event_list_page.dart';

import '../../support/mobile_viewport.dart';

F1Event _event(String id, String date, String status) => F1Event(
      eventId: id,
      eventType: 'field',
      eventDate: date,
      status: status,
      week: 17,
      raceName: 'Formula 1 Heineken Silver Las Vegas Grand Prix',
      venueName: 'Autodromo Internazionale Enzo e Dino Ferrari',
      venueCity: 'Imola',
      venueState: 'Emilia-Romagna',
      participants: const [],
    );

Widget _page() => ProviderScope(
      overrides: [
        f1EventsListProvider.overrideWith((ref, query) async => [_event('1', '2026-09-06', query.status), _event('2', '2026-09-13', query.status)]),
        f1LiveScoresProvider.overrideWith((ref, sport) => Future.value(const {})),
      ],
      child: const MaterialApp(home: Scaffold(body: F1EventListPage(sportId: 'f1'))),
    );

void main() {
  for (final width in mobileViewportWidths) {
    testWidgets('upcoming races render with no overflow or clipped text at ${width}px wide', (tester) async {
      await pumpAtWidth(tester, width, _page());
      expect(tester.takeException(), isNull);
      await tester.pump(const Duration(seconds: 31));
    });

    testWidgets('completed races render with no overflow or clipped text at ${width}px wide', (tester) async {
      await pumpAtWidth(tester, width, _page());
      await tester.tap(find.text('Completed'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(truncatedText(tester), isEmpty);
      await tester.pump(const Duration(seconds: 31));
    });
  }
}
