import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

import 'package:front_end/core/widgets/event_card.dart';

void main() {
  group('EventCard', () {
    testWidgets('renders the title, date label, and venue label', (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: EventCard(
            sport: 'nfl',
            eventId: '1',
            title: const Text('Chiefs vs Chargers'),
            dateLabel: 'Sep 28, 2025',
            isCompleted: false,
            venueLabel: 'Arrowhead Stadium -- Kansas City, MO',
          ),
        ),
      ));

      expect(find.text('Chiefs vs Chargers'), findsOneWidget);
      expect(find.text('Sep 28, 2025'), findsOneWidget);
      expect(find.text('Arrowhead Stadium -- Kansas City, MO'), findsOneWidget);
      expect(find.byIcon(Icons.location_on_outlined), findsOneWidget);
    });

    testWidgets('omits the venue row entirely when venueLabel is null', (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: EventCard(sport: 'nfl', eventId: '1', title: const Text('Chiefs vs Chargers'), dateLabel: 'Sep 28, 2025', isCompleted: false),
        ),
      ));

      expect(find.byIcon(Icons.location_on_outlined), findsNothing);
    });

    testWidgets('shows UPCOMING when not completed and trailing is not overridden', (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: EventCard(sport: 'nfl', eventId: '1', title: const Text('Chiefs vs Chargers'), dateLabel: 'Sep 28, 2025', isCompleted: false),
        ),
      ));

      expect(find.text('UPCOMING'), findsOneWidget);
      expect(find.text('FINAL'), findsNothing);
    });

    testWidgets('shows FINAL when completed and trailing is not overridden', (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: EventCard(sport: 'nfl', eventId: '1', title: const Text('Chiefs vs Chargers'), dateLabel: 'Sep 28, 2025', isCompleted: true),
        ),
      ));

      expect(find.text('FINAL'), findsOneWidget);
      expect(find.text('UPCOMING'), findsNothing);
    });

    testWidgets('a custom trailing widget replaces the default status pill', (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: EventCard(
            sport: 'nfl',
            eventId: '1',
            title: const Text('Chiefs vs Chargers'),
            dateLabel: 'Sep 28, 2025',
            isCompleted: false,
            trailing: const Text('LIVE'),
          ),
        ),
      ));

      expect(find.text('LIVE'), findsOneWidget);
      expect(find.text('UPCOMING'), findsNothing);
      expect(find.text('FINAL'), findsNothing);
    });

    testWidgets('tapping the card navigates to the event detail route', (tester) async {
      final router = GoRouter(routes: [
        GoRoute(path: '/', builder: (context, state) => Scaffold(
              body: EventCard(sport: 'nfl', eventId: '401547417', title: const Text('Chiefs vs Chargers'), dateLabel: 'Sep 28, 2025', isCompleted: false),
            )),
        GoRoute(path: '/nfl/events/401547417', builder: (context, state) => const Scaffold(body: Text('Detail Page'))),
      ]);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));

      await tester.tap(find.text('Chiefs vs Chargers'));
      await tester.pumpAndSettle();

      expect(find.text('Detail Page'), findsOneWidget);
    });
  });
}
