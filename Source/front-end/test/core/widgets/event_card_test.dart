import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

import 'package:front_end/core/widgets/event_card.dart';

import '../../support/mobile_viewport.dart';

Widget _routed(Widget card) {
  final router = GoRouter(routes: [
    GoRoute(path: '/', builder: (_, __) => Scaffold(body: SingleChildScrollView(child: card))),
    GoRoute(path: '/f1/events/:id', builder: (_, state) => Text('detail ${state.pathParameters['id']}')),
  ]);
  return ProviderScope(child: MaterialApp.router(routerConfig: router));
}

const _sessions = [
  EventSession(eventId: 'sprint', label: 'SPRINT · SAT OCT 17'),
  EventSession(eventId: 'race', label: 'GRAND PRIX · SUN OCT 18'),
];

void main() {
  test('dateBlockLabels shows a day, or a day range, over the month', () {
    expect(dateBlockLabels(DateTime(2026, 10, 22), null), ('22', 'OCT'));
    expect(dateBlockLabels(DateTime(2026, 10, 8), DateTime(2026, 10, 11)), ('8–11', 'OCT'));
    expect(dateBlockLabels(DateTime(2026, 10, 30), DateTime(2026, 11, 2)), ('30–2', 'OCT'));
    expect(dateBlockLabels(DateTime(2026, 10, 18), DateTime(2026, 10, 18)), ('18', 'OCT'));
  });

  test('startsInLabel counts whole days and is null once the event has started', () {
    final now = DateTime(2026, 10, 10, 23, 30);
    expect(startsInLabel(DateTime(2026, 10, 10), now), 'TODAY');
    expect(startsInLabel(DateTime(2026, 10, 11), now), 'TOMORROW');
    expect(startsInLabel(DateTime(2026, 10, 22), now), 'IN 12 DAYS');
    expect(startsInLabel(DateTime(2026, 10, 8), now), isNull);
  });

  group('EventCard', () {
    testWidgets('shows the date block, title, venue and trailing, and opens its event on tap', (tester) async {
      await tester.pumpWidget(_routed(const EventCard(
        sport: 'f1', eventId: 'race', title: Text('Mexico City Grand Prix'), dateBlock: ('25', 'OCT'),
        venueLabel: 'Autódromo Hermanos Rodríguez', trailing: Text('IN 15 DAYS'),
      )));

      expect(find.text('25'), findsOneWidget);
      expect(find.text('OCT'), findsOneWidget);
      expect(find.text('Autódromo Hermanos Rodríguez'), findsOneWidget);
      expect(find.text('IN 15 DAYS'), findsOneWidget);
      // The tab already says upcoming or completed.
      expect(find.text('UPCOMING'), findsNothing);
      expect(find.text('FINAL'), findsNothing);

      await tester.tap(find.text('Mexico City Grand Prix'));
      await tester.pumpAndSettle();
      expect(find.text('detail race'), findsOneWidget);
    });

    testWidgets('leaves out the venue line and date block when there is none', (tester) async {
      await tester.pumpWidget(_routed(const EventCard(sport: 'f1', eventId: 'race', title: Text('Grand Prix'), dateBlock: null)));

      expect(find.byIcon(Icons.location_on_outlined), findsNothing);
      expect(tester.takeException(), isNull);
    });

    testWidgets('each session chip opens that session, not the card\'s own event', (tester) async {
      await tester.pumpWidget(_routed(const EventCard(
        sport: 'f1', eventId: 'race', title: Text('United States Grand Prix'), dateBlock: ('17–18', 'OCT'), sessions: _sessions,
      )));

      await tester.tap(find.text('SPRINT · SAT OCT 17'));
      await tester.pumpAndSettle();
      expect(find.text('detail sprint'), findsOneWidget);
    });
  });

  group('FeaturedEventCard', () {
    FeaturedEventCard card(AsyncValue<List<FeaturedPick>>? picks) => FeaturedEventCard(
          sport: 'f1', eventId: 'sprint', eyebrow: 'NEXT · IN 7 DAYS', title: 'United States Grand Prix',
          venueLabel: 'Circuit of the Americas -- Austin, TX', sessions: _sessions,
          picksLabel: 'SPRINT WIN CHANCE', picks: picks,
        );

    const picks = [
      FeaturedPick(name: 'Lando Norris', detail: 'McLaren', value: '34%'),
      FeaturedPick(name: 'Max Verstappen', detail: 'Red Bull', value: '27%'),
      FeaturedPick(name: 'Oscar Piastri', detail: 'McLaren', value: '19%'),
    ];

    testWidgets('lists the model\'s picks beside the event on a wide screen', (tester) async {
      await tester.pumpWidget(_routed(card(const AsyncData(picks))));

      expect(find.text('NEXT · IN 7 DAYS'), findsOneWidget);
      expect(find.text('SPRINT WIN CHANCE'), findsOneWidget);
      expect(find.text('34%'), findsOneWidget);
      expect(find.textContaining('Lando Norris', findRichText: true), findsOneWidget);
      expect(
        tester.getTopLeft(find.text('SPRINT WIN CHANCE')).dx,
        greaterThan(tester.getTopRight(find.text('United States Grand Prix')).dx),
      );
    });

    testWidgets('shows without picks while they load, when they fail, and when there are none', (tester) async {
      for (final state in <AsyncValue<List<FeaturedPick>>?>[
        const AsyncLoading(), AsyncError('boom', StackTrace.empty), const AsyncData([]), null,
      ]) {
        await tester.pumpWidget(_routed(card(state)));

        expect(find.text('United States Grand Prix'), findsOneWidget);
        expect(find.text('SPRINT WIN CHANCE'), findsNothing);
      }
    });

    for (final width in mobileViewportWidths) {
      testWidgets('stacks the picks under the event and fits a ${width}px phone', (tester) async {
        await pumpAtWidth(tester, width, _routed(card(const AsyncData(picks))));

        expect(tester.takeException(), isNull);
        expect(
          tester.getTopLeft(find.text('SPRINT WIN CHANCE')).dy,
          greaterThan(tester.getBottomLeft(find.text('United States Grand Prix')).dy),
        );
      });
    }
  });
}
