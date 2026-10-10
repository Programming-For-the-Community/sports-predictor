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
    /// Opens `group`'s menu, toggles `choice` in it, and closes the menu.
    Future<void> pick(WidgetTester tester, String group, String choice) async {
      await tester.tap(find.textContaining(group).first);
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(CheckboxMenuButton, choice));
      await tester.pumpAndSettle();
      await tester.tap(find.textContaining(group).first);
      await tester.pumpAndSettle();
    }

    testWidgets('a day choice keeps only games on that weekday, and Clear brings the rest back', (tester) async {
      await _pumpPage(
        tester,
        [_event('thu', hour: 13, day: 24), _event('sun', hour: 13), _event('sun2', hour: 13)],
        {'thu': 0.7, 'sun': 0.7, 'sun2': 0.7},
      );

      await pick(tester, 'DAY', 'SUN');

      expect(_rowCount(tester), 2);
      // The button names the one active choice, and the count says what's left.
      expect(find.text('DAY · SUN'), findsOneWidget);
      expect(find.text('2 OF 3 GAMES'), findsOneWidget);

      await tester.tap(find.text(EventListFilters.clearLabel));
      await tester.pump();

      expect(_rowCount(tester), 3);
      expect(find.text('DAY'), findsOneWidget);
    });

    testWidgets('a menu lists each choice with its game count and stays open across toggles', (tester) async {
      await _pumpPage(
        tester,
        [_event('thu', hour: 13, day: 24), _event('sun', hour: 13), _event('sun2', hour: 13)],
        {'thu': 0.7, 'sun': 0.7, 'sun2': 0.7},
      );

      await tester.tap(find.text('DAY'));
      await tester.pumpAndSettle();
      final sunday = find.widgetWithText(CheckboxMenuButton, 'SUN');
      expect(find.descendant(of: sunday, matching: find.text('2')), findsOneWidget);
      expect(find.descendant(of: find.widgetWithText(CheckboxMenuButton, 'THU'), matching: find.text('1')), findsOneWidget);

      await tester.tap(sunday);
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(CheckboxMenuButton, 'THU'));
      await tester.pumpAndSettle();

      expect(_rowCount(tester), 3);
      expect(find.text('DAY · 2'), findsOneWidget);
    });

    testWidgets('no day menu when every game is on the same day', (tester) async {
      await _pumpPage(tester, [_event('a', hour: 12), _event('b', hour: 19)], {'a': 0.7, 'b': 0.7});

      expect(find.text('DAY'), findsNothing);
      expect(find.text('START'), findsOneWidget);
    });

    testWidgets('a confidence choice keeps only games in that tier, and keeps games still loading with a note', (tester) async {
      await _pumpPage(
        tester,
        [_event('high', hour: 13), _event('med', hour: 13), _event('low', hour: 13), _event('loading', hour: 13)],
        {'high': ConfidenceTier.high.minProbability, 'med': ConfidenceTier.med.minProbability, 'low': 0.5, 'loading': null},
      );
      expect(_rowCount(tester), 4);

      await pick(tester, 'CONFIDENCE', 'HIGH');

      expect(_rowCount(tester), 2);
      expect(find.textContaining('1 game still loading a prediction'), findsOneWidget);

      await pick(tester, 'CONFIDENCE', 'MED');

      expect(_rowCount(tester), 3);
    });

    testWidgets('a start-time choice keeps only games in that slot', (tester) async {
      await _pumpPage(
        tester,
        [_event('early', hour: 12), _event('late', hour: 19, minute: 30), _event('late2', hour: 19, minute: 30)],
        {'early': 0.7, 'late': 0.7, 'late2': 0.7},
      );

      await pick(tester, 'START', '7:30 PM');
      expect(_rowCount(tester), 2);

      await pick(tester, 'START', '7:30 PM');
      expect(_rowCount(tester), 3);
    });

    testWidgets('no start menu when every game starts in the same hour', (tester) async {
      await _pumpPage(tester, [_event('a', hour: 13), _event('b', hour: 13)], {'a': 0.7, 'b': 0.7});

      expect(find.text('START'), findsNothing);
      expect(find.text('CONFIDENCE'), findsOneWidget);
    });

    testWidgets('the menus sit on one row on a wide screen', (tester) async {
      tester.view.physicalSize = const Size(1200, 900);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);
      await _pumpPage(tester, [_event('a', hour: 12), _event('b', hour: 19)], {'a': 0.7, 'b': 0.7});

      final confidence = tester.getRect(find.text('CONFIDENCE'));
      final start = tester.getRect(find.text('START'));
      expect(start.center.dy, closeTo(confidence.center.dy, 2));
      expect(start.left, greaterThan(confidence.right));
    });

    testWidgets('says so when the filters leave nothing', (tester) async {
      await _pumpPage(tester, [_event('a', hour: 13)], {'a': 0.5});

      await pick(tester, 'CONFIDENCE', 'HIGH');

      expect(_rowCount(tester), 0);
      expect(find.text('No games match these filters.'), findsOneWidget);
    });

    testWidgets('the Completed tab has no game filters', (tester) async {
      await _pumpPage(tester, [_event('a', hour: 13)], {'a': 0.7});

      await tester.tap(find.text('Completed'));
      await tester.pumpAndSettle();

      expect(find.text('CONFIDENCE'), findsNothing);
    });

    testWidgets('on a phone one Filters button opens a sheet, and active choices stay as removable chips', (tester) async {
      tester.view.physicalSize = const Size(360, 900);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);
      await _pumpPage(
        tester,
        [_event('thu', hour: 13, day: 24), _event('sun', hour: 13), _event('sun2', hour: 19)],
        {'thu': 0.7, 'sun': 0.7, 'sun2': 0.7},
      );
      expect(tester.takeException(), isNull);
      expect(find.text('DAY'), findsNothing);

      await tester.tap(find.text(EventListFilters.filtersLabel));
      await tester.pumpAndSettle();
      // Every group is in the sheet.
      expect(find.text('CONFIDENCE'), findsOneWidget);
      expect(find.text('DAY'), findsOneWidget);
      expect(find.text('START'), findsOneWidget);

      await tester.tap(find.text('SUN'));
      await tester.pumpAndSettle();
      await tester.tap(find.text(EventListFilters.doneLabel));
      await tester.pumpAndSettle();

      expect(tester.takeException(), isNull);
      expect(_rowCount(tester), 2);
      expect(find.text('${EventListFilters.filtersLabel} · 1'), findsOneWidget);

      // The chip removes its own choice.
      await tester.tap(find.text('SUN'));
      await tester.pump();
      expect(_rowCount(tester), 3);
    });
  });
}
