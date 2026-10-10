import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/f1_events_repository.dart';
import 'package:front_end/core/models/f1_event.dart';
import 'package:front_end/core/models/f1_live_score.dart';
import 'package:front_end/core/models/f1_prediction.dart';
import 'package:front_end/core/widgets/event_card.dart';
import 'package:front_end/core/widgets/f1_event_row.dart';
import 'package:front_end/core/widgets/live_status_pill.dart';
import 'package:front_end/core/widgets/sprint_badge.dart';

import '../../support/mobile_viewport.dart';

F1Event _event(String id, {String type = 'field', String date = '2026-10-18', String status = 'scheduled', int? week = 19}) => F1Event(
      eventId: id, eventType: type, eventDate: date, status: status, participants: const [],
      season: 2026, week: week, raceName: 'United States Grand Prix', venueName: 'Circuit of the Americas',
    );

final _now = DateTime(2026, 10, 10);

F1DriverPrediction _driver(String name, double win) => F1DriverPrediction(
      entityId: name, name: name, constructorName: 'McLaren', winProbability: F1ModelValue(value: win, modelVersion: 1),
    );

Widget _wrap(Widget child, {F1EventPrediction? prediction}) => ProviderScope(
      overrides: [
        f1EventPredictionProvider.overrideWith((ref, query) async => prediction ?? (throw Exception('no prediction'))),
      ],
      child: MaterialApp(home: Scaffold(body: SingleChildScrollView(child: child))),
    );

void main() {
  group('groupF1Weekends', () {
    test('a sprint and its Grand Prix become one weekend, sprint first, where the first of them sat', () {
      final weekends = groupF1Weekends([
        _event('race', date: '2026-10-18'),
        _event('mexico', date: '2026-10-25', week: 20),
        _event('sprint', type: 'sprint', date: '2026-10-17'),
      ]);

      expect(weekends.map((w) => w.sessions.map((e) => e.eventId).toList()), [['sprint', 'race'], ['mexico']]);
      expect(weekends.first.hasBothSessions, isTrue);
      expect(weekends.first.main.eventId, 'race');
      expect(weekends.first.chips.map((c) => c.label), ['SPRINT · SAT OCT 17', 'GRAND PRIX · SUN OCT 18']);
      expect(weekends.last.chips, isEmpty);
    });

    test('the card opens the first session not yet completed, and an event with no round stands alone', () {
      final weekend = groupF1Weekends([
        _event('sprint', type: 'sprint', date: '2026-10-17', status: 'completed'),
        _event('race'),
      ]).single;
      expect(weekend.next.eventId, 'race');

      expect(groupF1Weekends([_event('a', week: null), _event('b', week: null)]), hasLength(2));
    });
  });

  group('F1EventRow', () {
    testWidgets('a sprint weekend is one card with a chip per session and no badge', (tester) async {
      final weekend = groupF1Weekends([_event('sprint', type: 'sprint', date: '2026-10-17'), _event('race')]).single;
      await tester.pumpWidget(_wrap(F1EventRow(sport: 'f1', weekend: weekend, now: _now)));

      expect(find.text('United States Grand Prix'), findsOneWidget);
      expect(find.text('17–18'), findsOneWidget);
      expect(find.text('SPRINT · SAT OCT 17'), findsOneWidget);
      expect(find.text('GRAND PRIX · SUN OCT 18'), findsOneWidget);
      expect(find.byType(SprintBadge), findsNothing);
      expect(find.text('IN 7 DAYS'), findsOneWidget);
    });

    testWidgets('an ordinary weekend has no chips, and a lone sprint keeps its badge', (tester) async {
      await tester.pumpWidget(_wrap(F1EventRow(sport: 'f1', weekend: F1Weekend([_event('race')]), now: _now)));
      expect(find.byType(EventSessionChips), findsNothing);
      expect(find.byType(SprintBadge), findsNothing);

      await tester.pumpWidget(_wrap(F1EventRow(sport: 'f1', weekend: F1Weekend([_event('sprint', type: 'sprint')]), now: _now)));
      expect(find.byType(SprintBadge), findsOneWidget);
    });

    testWidgets('a live session shows LIVE, and a finished weekend shows its year', (tester) async {
      final weekend = F1Weekend([_event('race')]);
      await tester.pumpWidget(_wrap(F1EventRow(
        sport: 'f1', weekend: weekend, now: _now,
        liveScores: const {'race': F1LiveEventState(eventType: 'field', state: 'in', participants: {})},
      )));
      expect(find.byType(LiveStatusPill), findsOneWidget);

      // ESPN reports it over before storage does.
      await tester.pumpWidget(_wrap(F1EventRow(
        sport: 'f1', weekend: weekend, now: _now,
        liveScores: const {'race': F1LiveEventState(eventType: 'field', state: 'post', participants: {})},
      )));
      expect(find.text('2026'), findsOneWidget);
      expect(find.byType(LiveStatusPill), findsNothing);
    });
  });

  group('FeaturedF1Weekend', () {
    final weekend = groupF1Weekends([_event('sprint', type: 'sprint', date: '2026-10-17'), _event('race')]).single;
    final prediction = F1EventPrediction(
      eventId: 'sprint', eventType: 'sprint',
      field: [_driver('Lando Norris', 0.34), _driver('Max Verstappen', 0.27), _driver('Oscar Piastri', 0.19), _driver('George Russell', 0.08)],
      constructors: const [],
    );

    testWidgets('names the next session and lists the model\'s top three for it', (tester) async {
      await tester.pumpWidget(_wrap(FeaturedF1Weekend(sport: 'f1', weekend: weekend, now: _now), prediction: prediction));
      await tester.pumpAndSettle();

      expect(find.text('NEXT · IN 7 DAYS'), findsOneWidget);
      expect(find.text(FeaturedF1Weekend.picksLabel(true)), findsOneWidget);
      expect(find.text('34%'), findsOneWidget);
      expect(find.text('19%'), findsOneWidget);
      expect(find.text('8%'), findsNothing);
      expect(find.text('SPRINT · SAT OCT 17'), findsOneWidget);
    });

    testWidgets('shows the weekend without picks when the prediction fails', (tester) async {
      await tester.pumpWidget(_wrap(FeaturedF1Weekend(sport: 'f1', weekend: weekend, now: _now)));
      await tester.pumpAndSettle();

      expect(find.text('United States Grand Prix'), findsOneWidget);
      expect(find.text(FeaturedF1Weekend.picksLabel(true)), findsNothing);
    });

    for (final width in mobileViewportWidths) {
      testWidgets('fits a ${width}px phone', (tester) async {
        await pumpAtWidth(
          tester, width, _wrap(FeaturedF1Weekend(sport: 'f1', weekend: weekend, now: _now), prediction: prediction),
        );
        await tester.pumpAndSettle();

        expect(tester.takeException(), isNull);
      });
    }
  });
}
