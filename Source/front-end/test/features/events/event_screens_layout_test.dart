import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/events_repository.dart';
import 'package:front_end/core/data/f1_events_repository.dart';
import 'package:front_end/core/data/field_events_repository.dart';
import 'package:front_end/core/data/live_scores_repository.dart';
import 'package:front_end/core/models/event.dart';
import 'package:front_end/core/models/event_leaders.dart';
import 'package:front_end/core/models/f1_event.dart';
import 'package:front_end/core/models/f1_live_score.dart';
import 'package:front_end/core/models/f1_prediction.dart';
import 'package:front_end/core/models/field_event.dart';
import 'package:front_end/core/models/field_prediction.dart';
import 'package:front_end/core/models/live_score.dart';
import 'package:front_end/core/widgets/event_card.dart';
import 'package:front_end/core/widgets/field_event_row.dart';
import 'package:front_end/core/widgets/matchup_hero.dart';
import 'package:front_end/core/widgets/pick_labels.dart';
import 'package:front_end/core/widgets/sprint_badge.dart';
import 'package:front_end/core/widgets/team_leaders_panel.dart';
import 'package:front_end/features/events/event_detail_page.dart';
import 'package:front_end/features/events/event_list_page.dart';
import 'package:front_end/features/events/f1_event_detail_page.dart';
import 'package:front_end/features/events/f1_event_list_page.dart';
import 'package:front_end/features/events/field_event_list_page.dart';

import '../../support/mobile_viewport.dart';

PredictionComparison _comparison({required bool correct}) => PredictionComparison(
      predictedHomeWinProbability: 0.67, predictedHomeWon: true, actualHomeWon: correct, correct: correct,
      predictedMargin: 10, actualMargin: correct ? 14 : -3, predictedHomeScore: 27, predictedAwayScore: 17,
      actualHomeScore: correct ? 31 : 17, actualAwayScore: correct ? 17 : 20,
    );

const _leadersComparison = EventLeadersComparison(
  home: TeamLeadersComparison({
    'passing': [PlayerStatLineComparison(entityId: '1', name: 'QB One', predicted: {'passing_yards': 241}, actual: {'passing_yards': 268})],
    'rushing': [], 'receiving': [], 'sacks': [],
  }),
  away: TeamLeadersComparison({'passing': [], 'rushing': [], 'receiving': [], 'sacks': []}),
);

SportEvent _completed(String id, String kickoff, {required bool correct, EventLeadersComparison? leaders}) => SportEvent(
      eventId: id, eventDate: kickoff.split('T').first, kickoffTime: kickoff, status: 'completed', week: 2, round: null,
      participants: [
        Participant(entityId: '12', role: 'home', result: ParticipantResult(score: correct ? 31 : 17, won: correct)),
        Participant(entityId: '13', role: 'away', result: ParticipantResult(score: correct ? 17 : 20, won: !correct)),
      ],
      predictionComparison: _comparison(correct: correct), leadersComparison: leaders,
    );

void _size(WidgetTester tester, double width) {
  tester.view.physicalSize = Size(width, 1400);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
}

void main() {
  group('game list', () {
    Widget page(List<SportEvent> completed) => ProviderScope(
          overrides: [
            eventsListProvider.overrideWith((ref, query) async => query.status == 'completed' ? completed : const <SportEvent>[]),
            liveScoresProvider.overrideWith((ref, sport) async => const <String, LiveEventState>{}),
          ],
          child: const MaterialApp(home: Scaffold(body: EventListPage(sportId: 'nfl'))),
        );

    testWidgets('carries the colour key, and each completed day shows the model\'s record', (tester) async {
      await tester.pumpWidget(page([
        _completed('1', '2026-09-13T17:00:00Z', correct: true),
        _completed('2', '2026-09-13T20:00:00Z', correct: true),
        _completed('3', '2026-09-13T23:00:00Z', correct: false),
        _completed('4', '2026-09-10T23:00:00Z', correct: false),
      ]));
      await tester.pumpAndSettle();
      expect(find.byType(ActualModelKey), findsOneWidget);

      await tester.tap(find.text('Completed'));
      await tester.pumpAndSettle();

      expect(find.text('MODEL 2–1'), findsOneWidget);
      expect(find.text('MODEL 0–1'), findsOneWidget);
    });
  });

  group('game page', () {
    Widget page() => ProviderScope(
          overrides: [
            eventsListProvider.overrideWith((ref, query) async => query.status == 'completed'
                ? [_completed('1', '2026-09-13T17:00:00Z', correct: true, leaders: _leadersComparison)]
                : const <SportEvent>[]),
            liveScoresProvider.overrideWith((ref, sport) async => const <String, LiveEventState>{}),
          ],
          child: const MaterialApp(home: Scaffold(body: EventDetailPage(sportId: 'nfl', eventId: '1'))),
        );

    testWidgets('puts the matchup beside the player props on a wide screen', (tester) async {
      _size(tester, 1300);
      await tester.pumpWidget(page());
      await tester.pumpAndSettle();

      final hero = tester.getRect(find.byType(MatchupResultHero));
      final props = tester.getRect(find.byType(TeamLeadersComparisonPanel));
      expect(props.left, greaterThan(hero.right));
      expect(props.top, closeTo(hero.top, 1));
    });

    for (final width in mobileViewportWidths) {
      testWidgets('stacks them on a ${width}px phone with no overflow', (tester) async {
        await pumpAtWidth(tester, width, page());
        await tester.pumpAndSettle();

        expect(tester.takeException(), isNull);
        final hero = tester.getRect(find.byType(MatchupResultHero));
        final props = tester.getRect(find.byType(TeamLeadersComparisonPanel));
        expect(props.top, greaterThan(hero.bottom));
      });
    }
  });

  group('PGA list', () {
    FieldEvent event(String id, String start, String end, {String status = 'scheduled'}) => FieldEvent(
          eventId: id, eventType: 'field', eventDate: start, endDate: end, status: status, participants: const [],
          tournamentName: 'Tournament $id With A Long Sponsor Name Championship', venueName: 'Yokohama Country Club',
        );

    final prediction = PgaFieldPrediction(FieldEventPrediction(eventId: 'a', field: [
      for (final (name, chance) in [('Xander Schauffele', 0.61), ('Collin Morikawa', 0.55), ('Hideki Matsuyama', 0.52), ('Fourth', 0.4)])
        FieldParticipantPrediction(entityId: name, name: name, top10Probability: ModelValue(value: chance, modelVersion: 1)),
    ]));

    Widget page({PgaEventPrediction? forNext}) => ProviderScope(
          overrides: [
            fieldEventsListProvider.overrideWith((ref, query) async => query.status == 'completed'
                ? [event('done', '2025-10-01', '2025-10-04', status: 'completed')]
                : [event('b', '2030-10-22', '2030-10-25'), event('a', '2030-10-08', '2030-10-11')]),
            fieldEventPredictionProvider.overrideWith((ref, query) async => forNext ?? (throw Exception('none'))),
          ],
          child: const MaterialApp(home: Scaffold(body: FieldEventListPage(sportId: 'pga'))),
        );

    testWidgets('features the next tournament with the model\'s top three, and only that one fetches a prediction', (tester) async {
      await tester.pumpWidget(page(forNext: prediction));
      await tester.pumpAndSettle();

      expect(find.byType(FeaturedEventCard), findsOneWidget);
      expect(find.byType(FieldEventRow), findsOneWidget);
      // The soonest one is featured.
      expect(find.descendant(of: find.byType(FeaturedEventCard), matching: find.textContaining('Tournament a')), findsOneWidget);
      expect(find.text(FeaturedFieldEvent.picksLabel), findsOneWidget);
      expect(find.text('61%'), findsOneWidget);
      expect(find.text('40%'), findsNothing);
      expect(find.text('UPCOMING'), findsNothing);
    });

    testWidgets('a cup, with no field to rank, is featured without picks', (tester) async {
      final cup = PgaTwoSidedPrediction(const TwoSidedPgaPrediction(eventId: 'a', eventType: 'cup'));
      await tester.pumpWidget(page(forNext: cup));
      await tester.pumpAndSettle();

      expect(find.byType(FeaturedEventCard), findsOneWidget);
      expect(find.text(FeaturedFieldEvent.picksLabel), findsNothing);
    });

    testWidgets('completed rows show the year and no featured card', (tester) async {
      await tester.pumpWidget(page());
      await tester.pumpAndSettle();
      await tester.tap(find.text('Completed'));
      await tester.pumpAndSettle();

      expect(find.byType(FeaturedEventCard), findsNothing);
      expect(find.text('2025'), findsOneWidget);
      expect(find.text('1–4'), findsOneWidget);
    });

    testWidgets('a tournament under way reads IN PROGRESS', (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: FieldEventRow(sport: 'pga', event: event('x', '2026-10-08', '2026-10-11'), now: DateTime(2026, 10, 9))),
      ));

      expect(find.text(inProgressLabel), findsOneWidget);
    });

    for (final width in mobileViewportWidths) {
      testWidgets('fits a ${width}px phone', (tester) async {
        await pumpAtWidth(tester, width, page(forNext: prediction));
        await tester.pumpAndSettle();

        expect(tester.takeException(), isNull);
      });
    }
  });

  group('F1 sprint weekend', () {
    F1Event event(String id, String type, String date, {String status = 'scheduled', int week = 19}) => F1Event(
          eventId: id, eventType: type, eventDate: date, status: status, participants: const [],
          season: 2030, week: week, raceName: 'United States Grand Prix',
        );

    F1EventPrediction prediction(String id, String type) => F1EventPrediction(
          eventId: id, eventType: type, raceName: 'United States Grand Prix', season: 2030, week: 19,
          field: [F1DriverPrediction(entityId: 'norris', name: 'Lando Norris', winProbability: const F1ModelValue(value: 0.34, modelVersion: 1))],
          constructors: const [],
        );

    Widget scope(Widget page, {required List<F1Event> scheduled, List<F1Event> completed = const []}) => ProviderScope(
          overrides: [
            f1EventsListProvider.overrideWith((ref, query) async => query.status == 'completed' ? completed : scheduled),
            f1LiveScoresProvider.overrideWith((ref, sport) async => const <String, F1LiveEventState>{}),
            f1EventPredictionProvider.overrideWith(
              (ref, query) async => prediction(query.eventId, query.eventId == 'sprint' ? 'sprint' : 'field'),
            ),
          ],
          child: MaterialApp(home: Scaffold(body: page)),
        );

    testWidgets('the list shows one card for the weekend and a plain row for the next race', (tester) async {
      await tester.pumpWidget(scope(const F1EventListPage(sportId: 'f1'), scheduled: [
        event('race', 'field', '2030-10-18'),
        event('sprint', 'sprint', '2030-10-17'),
        event('mexico', 'field', '2030-10-25', week: 20),
      ]));
      await tester.pumpAndSettle();

      expect(find.byType(FeaturedEventCard), findsOneWidget);
      expect(find.byType(EventCard), findsOneWidget);
      expect(find.text('SPRINT · THU OCT 17'), findsOneWidget);
      expect(find.text('GRAND PRIX · FRI OCT 18'), findsOneWidget);
      // The featured picks are for the sprint, the weekend's next session.
      expect(find.text('SPRINT WIN CHANCE'), findsOneWidget);
      expect(find.byType(SprintBadge), findsNothing);
    });

    testWidgets('the race page switches between the sprint and the Grand Prix, even once the sprint is completed', (tester) async {
      await tester.pumpWidget(scope(
        const F1EventDetailPage(sportId: 'f1', eventId: 'sprint'),
        scheduled: [event('race', 'field', '2030-10-18')],
        completed: [event('sprint', 'sprint', '2030-10-17', status: 'completed')],
      ));
      await tester.pumpAndSettle();

      expect(find.byType(EventSessionChips), findsOneWidget);
      expect(find.text('SPRINT · THU OCT 17'), findsOneWidget);
      expect(find.text('GRAND PRIX · FRI OCT 18'), findsOneWidget);
      // The switch names the session, so the badge is not repeated.
      expect(find.byType(SprintBadge), findsNothing);
    });

    testWidgets('an ordinary weekend has no switch, and a lone sprint keeps its badge', (tester) async {
      await tester.pumpWidget(scope(
        const F1EventDetailPage(sportId: 'f1', eventId: 'sprint'), scheduled: [event('sprint', 'sprint', '2030-10-17')],
      ));
      await tester.pumpAndSettle();

      expect(find.byType(EventSessionChips), findsNothing);
      expect(find.byType(SprintBadge), findsOneWidget);
    });
  });
}
