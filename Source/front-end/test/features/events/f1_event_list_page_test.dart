import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/f1_events_repository.dart';
import 'package:front_end/core/data/live_scores_repository.dart';
import 'package:front_end/core/models/f1_event.dart';
import 'package:front_end/core/models/f1_live_score.dart';
import 'package:front_end/features/events/f1_event_list_page.dart';

F1Event _event(String id, String eventDate, {String raceName = 'Grand Prix'}) => F1Event(
      eventId: id,
      eventType: 'field',
      eventDate: eventDate,
      status: 'scheduled',
      raceName: raceName,
      participants: const [],
    );

Widget _page({
  required List<F1Event> Function(String status) eventsFor,
  Future<Map<String, F1LiveEventState>> Function()? liveScores,
}) {
  return ProviderScope(
    overrides: [
      f1EventsListProvider.overrideWith((ref, query) async => eventsFor(query.status)),
      f1LiveScoresProvider.overrideWith((ref, sport) => liveScores?.call() ?? Future.value(const {})),
    ],
    child: const MaterialApp(home: Scaffold(body: F1EventListPage(sportId: 'f1'))),
  );
}

void main() {
  group('F1EventListPage', () {
    testWidgets('shows Upcoming races sorted soonest-first by default', (tester) async {
      await tester.pumpWidget(_page(eventsFor: (status) => status == 'scheduled'
          ? [_event('2', '2026-09-13', raceName: 'Italian Grand Prix'), _event('1', '2026-09-06', raceName: 'Dutch Grand Prix')]
          : []));
      await tester.pumpAndSettle();

      final dutchCenter = tester.getCenter(find.text('Dutch Grand Prix'));
      final italianCenter = tester.getCenter(find.text('Italian Grand Prix'));
      expect(dutchCenter.dy, lessThan(italianCenter.dy));

      await tester.pump(const Duration(seconds: 31)); // let the live-scores poll's pending timer resolve
    });

    testWidgets('switching to Completed shows completed races sorted most-recent-first', (tester) async {
      await tester.pumpWidget(_page(eventsFor: (status) => status == 'completed'
          ? [_event('1', '2026-08-23', raceName: 'Dutch Grand Prix'), _event('2', '2026-08-30', raceName: 'Italian Grand Prix')]
          : []));
      await tester.pumpAndSettle();

      await tester.tap(find.text('Completed'));
      await tester.pumpAndSettle();

      final dutchCenter = tester.getCenter(find.text('Dutch Grand Prix'));
      final italianCenter = tester.getCenter(find.text('Italian Grand Prix'));
      expect(italianCenter.dy, lessThan(dutchCenter.dy));
    });

    testWidgets('shows Coming Soon for an empty Upcoming list', (tester) async {
      await tester.pumpWidget(_page(eventsFor: (status) => []));
      await tester.pumpAndSettle();

      expect(find.text('Coming Soon'), findsOneWidget);
      await tester.pump(const Duration(seconds: 31));
    });

    testWidgets('shows a not-found message for an empty Completed list', (tester) async {
      await tester.pumpWidget(_page(eventsFor: (status) => []));
      await tester.pumpAndSettle();

      await tester.tap(find.text('Completed'));
      await tester.pumpAndSettle();

      expect(find.text('No races found.'), findsOneWidget);
    });

    testWidgets('shows a loading spinner while the list is fetching', (tester) async {
      await tester.pumpWidget(ProviderScope(
        overrides: [
          f1EventsListProvider.overrideWith((ref, query) => Completer<List<F1Event>>().future),
          f1LiveScoresProvider.overrideWith((ref, sport) async => const {}),
        ],
        child: const MaterialApp(home: Scaffold(body: F1EventListPage(sportId: 'f1'))),
      ));

      expect(find.byType(CircularProgressIndicator), findsOneWidget);
    });

    testWidgets('shows an error message when the fetch fails', (tester) async {
      await tester.pumpWidget(_page(eventsFor: (status) => throw Exception('network down')));
      await tester.pumpAndSettle();

      expect(find.textContaining('Couldn\'t load races'), findsOneWidget);
    });

    testWidgets('polls live scores every 30s while on the Upcoming tab', (tester) async {
      var liveScoresCalls = 0;
      await tester.pumpWidget(_page(
        eventsFor: (status) => status == 'scheduled' ? [_event('1', '2026-09-06')] : [],
        liveScores: () async {
          liveScoresCalls++;
          return const {};
        },
      ));
      await tester.pumpAndSettle();
      final initialCalls = liveScoresCalls;

      await tester.pump(const Duration(seconds: 31));
      expect(liveScoresCalls, greaterThan(initialCalls));
    });

    testWidgets('stops polling live scores once switched to Completed', (tester) async {
      var liveScoresCalls = 0;
      await tester.pumpWidget(_page(
        eventsFor: (status) => [],
        liveScores: () async {
          liveScoresCalls++;
          return const {};
        },
      ));
      await tester.pumpAndSettle();

      await tester.tap(find.text('Completed'));
      await tester.pumpAndSettle();
      final callsOnCompleted = liveScoresCalls;

      await tester.pump(const Duration(seconds: 31));
      expect(liveScoresCalls, callsOnCompleted);
    });

    testWidgets('refreshes live scores on resume while on Upcoming', (tester) async {
      var liveScoresCalls = 0;
      await tester.pumpWidget(_page(
        eventsFor: (status) => status == 'scheduled' ? [_event('1', '2026-09-06')] : [],
        liveScores: () async {
          liveScoresCalls++;
          return const {};
        },
      ));
      await tester.pumpAndSettle();
      final initialCalls = liveScoresCalls;

      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
      await tester.pumpAndSettle();

      expect(liveScoresCalls, greaterThan(initialCalls));
    });

    testWidgets('does not refresh live scores on resume while on Completed', (tester) async {
      var liveScoresCalls = 0;
      await tester.pumpWidget(_page(
        eventsFor: (status) => [],
        liveScores: () async {
          liveScoresCalls++;
          return const {};
        },
      ));
      await tester.pumpAndSettle();

      await tester.tap(find.text('Completed'));
      await tester.pumpAndSettle();
      final callsOnCompleted = liveScoresCalls;

      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
      await tester.pumpAndSettle();

      expect(liveScoresCalls, callsOnCompleted);
    });

    testWidgets('a race with live state shows LIVE instead of UPCOMING', (tester) async {
      await tester.pumpWidget(_page(
        eventsFor: (status) => status == 'scheduled' ? [_event('1', '2026-09-06')] : [],
        liveScores: () async => const {'1': F1LiveEventState(eventType: 'field', state: 'in')},
      ));
      await tester.pumpAndSettle();

      expect(find.text('LIVE'), findsOneWidget);
      await tester.pump(const Duration(seconds: 31));
    });
  });
}
