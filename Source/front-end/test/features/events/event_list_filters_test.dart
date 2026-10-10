import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/events_repository.dart';
import 'package:front_end/core/data/live_scores_repository.dart';
import 'package:front_end/core/models/event.dart';
import 'package:front_end/core/models/live_score.dart';
import 'package:front_end/core/models/prediction.dart';
import 'package:front_end/core/widgets/game_row.dart';
import 'package:front_end/features/events/event_list_filters.dart';
import 'package:front_end/features/events/event_list_page.dart';
import 'package:front_end/static/confidence_tiers.dart';

/// Kickoff given in the test machine's own local time, so slot labels don't
/// depend on where the tests run.
SportEvent _event(String id, {required int hour, int minute = 0, int day = 27}) {
  final kickoff = DateTime(2026, 9, day, hour, minute).toUtc().toIso8601String();
  return SportEvent(
    eventId: id,
    eventDate: kickoff.split('T').first,
    kickoffTime: kickoff,
    status: 'scheduled',
    week: 4,
    round: null,
    participants: const [
      Participant(entityId: '12', role: 'home', result: null),
      Participant(entityId: '13', role: 'away', result: null),
    ],
    predictionComparison: null,
    leadersComparison: null,
  );
}

EventPrediction _prediction(double homeWinProbability) => EventPrediction(
      homeWinProbability: homeWinProbability,
      homeWinProbabilityModelVersion: 1,
      margin: 3,
      homeScore: 24,
      awayScore: 21,
      leaders: null,
    );

Future<void> _pumpPage(WidgetTester tester, List<SportEvent> events, Map<String, double?> homeWinProbabilities) async {
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        eventsListProvider.overrideWith((ref, query) async => query.status == 'scheduled' ? events : const []),
        liveScoresProvider.overrideWith((ref, sport) async => const <String, LiveEventState>{}),
        eventPredictionProvider.overrideWith((ref, query) {
          final p = homeWinProbabilities[query.eventId];
          // Null stands in for a prediction that never finishes loading.
          return p == null ? Completer<EventPrediction>().future : Future.value(_prediction(p));
        }),
      ],
      child: const MaterialApp(home: Scaffold(body: EventListPage(sportId: 'nfl'))),
    ),
  );
  await tester.pump();
  await tester.pump();
}

int _rowCount(WidgetTester tester) => find.byType(GameRow).evaluate().length;

void main() {
  group('kickoffSlots', () {
    test('one chip per local hour, earliest first, showing the exact time when the hour has only one', () {
      final slots = kickoffSlots([
        _event('a', hour: 19),
        _event('b', hour: 15, minute: 30),
        _event('c', hour: 12),
        _event('d', hour: 12, minute: 45),
      ]);

      expect(slots.map((s) => s.hour), [12, 15, 19]);
      expect(slots.map((s) => s.label), ['12 PM', '3:30 PM', '7:00 PM']);
    });

    test('skips an event with no kickoff time', () {
      final noKickoff = SportEvent(
        eventId: 'x', eventDate: '2026-09-27', kickoffTime: null, status: 'scheduled', week: 4, round: null,
        participants: const [], predictionComparison: null, leadersComparison: null,
      );

      expect(kickoffSlots([noKickoff]), isEmpty);
    });
  });

  group('eventWeekdays', () {
    test('one entry per local weekday, in first-seen order, folding a later week into the same day', () {
      // Sep 24 2026 is a Thursday, 27 a Sunday, 28 a Monday.
      final weekdays = eventWeekdays([
        _event('thu', hour: 20, day: 24),
        _event('sun', hour: 13),
        _event('sun2', hour: 16),
        _event('mon', hour: 20, day: 28),
        _event('nextThu', hour: 20, day: 31), // rolls over to Oct 1
      ]);

      expect(weekdays, [DateTime.thursday, DateTime.sunday, DateTime.monday]);
      expect(weekdays.map(weekdayLabel), ['THU', 'SUN', 'MON']);
    });

    test('falls back to the day-only event date when there is no kickoff time', () {
      final noKickoff = SportEvent(
        eventId: 'x', eventDate: '2026-09-27', kickoffTime: null, status: 'scheduled', week: 4, round: null,
        participants: const [], predictionComparison: null, leadersComparison: null,
      );

      expect(eventWeekdays([noKickoff]), [DateTime.sunday]);
    });
  });

  group('EventListPage filters', () {
    testWidgets('a day chip keeps only games on that weekday, and selecting it again clears it', (tester) async {
      await _pumpPage(
        tester,
        [_event('thu', hour: 13, day: 24), _event('sun', hour: 13), _event('sun2', hour: 13)],
        {'thu': 0.7, 'sun': 0.7, 'sun2': 0.7},
      );

      await tester.tap(find.text('SUN').first);
      await tester.pump();
      expect(_rowCount(tester), 2);

      await tester.tap(find.text('SUN').first);
      await tester.pump();
      expect(_rowCount(tester), 3);
    });

    testWidgets('no day row when every game is on the same day', (tester) async {
      await _pumpPage(tester, [_event('a', hour: 12), _event('b', hour: 19)], {'a': 0.7, 'b': 0.7});

      expect(find.text('DAY'), findsNothing);
    });


    testWidgets('a confidence chip keeps only games in that tier, and keeps games still loading with a note', (tester) async {
      await _pumpPage(
        tester,
        [_event('high', hour: 13), _event('med', hour: 13), _event('low', hour: 13), _event('loading', hour: 13)],
        {'high': ConfidenceTier.high.minProbability, 'med': ConfidenceTier.med.minProbability, 'low': 0.5, 'loading': null},
      );
      expect(_rowCount(tester), 4);

      await tester.tap(find.text('HIGH').first);
      await tester.pump();

      expect(_rowCount(tester), 2);
      expect(find.textContaining('1 game still loading a prediction'), findsOneWidget);

      await tester.tap(find.text('MED').first);
      await tester.pump();

      expect(_rowCount(tester), 3);
    });

    testWidgets('a start-time chip keeps only games in that slot, and selecting it again clears it', (tester) async {
      await _pumpPage(
        tester,
        [_event('early', hour: 12), _event('late', hour: 19, minute: 30), _event('late2', hour: 19, minute: 30)],
        {'early': 0.7, 'late': 0.7, 'late2': 0.7},
      );

      await tester.tap(find.text('7:30 PM').first);
      await tester.pump();
      expect(_rowCount(tester), 2);

      await tester.tap(find.text('7:30 PM').first);
      await tester.pump();
      expect(_rowCount(tester), 3);
    });

    testWidgets('no start-time row when every game starts in the same hour', (tester) async {
      await _pumpPage(tester, [_event('a', hour: 13), _event('b', hour: 13)], {'a': 0.7, 'b': 0.7});

      expect(find.text('START TIME'), findsNothing);
      expect(find.text('WINNER CONFIDENCE'), findsOneWidget);
    });

    testWidgets('both filter groups sit on one row on a wide screen', (tester) async {
      tester.view.physicalSize = const Size(1200, 900);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);
      await _pumpPage(tester, [_event('a', hour: 12), _event('b', hour: 19)], {'a': 0.7, 'b': 0.7});

      final confidence = tester.getRect(find.text('WINNER CONFIDENCE'));
      final startTime = tester.getRect(find.text('START TIME'));
      expect(startTime.center.dy, closeTo(confidence.center.dy, 2));
      expect(startTime.left, greaterThan(confidence.right));
    });

    testWidgets('says so when the filters leave nothing', (tester) async {
      await _pumpPage(tester, [_event('a', hour: 13)], {'a': 0.5});

      await tester.tap(find.text('HIGH').first);
      await tester.pump();

      expect(_rowCount(tester), 0);
      expect(find.text('No games match these filters.'), findsOneWidget);
    });

    testWidgets('the Completed tab has no filters', (tester) async {
      await _pumpPage(tester, [_event('a', hour: 13)], {'a': 0.7});

      await tester.tap(find.text('Completed'));
      await tester.pumpAndSettle();

      expect(find.text('WINNER CONFIDENCE'), findsNothing);
    });
  });
}
