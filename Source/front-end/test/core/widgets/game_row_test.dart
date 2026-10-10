import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

import 'package:front_end/core/data/events_repository.dart';
import 'package:front_end/core/models/event.dart';
import 'package:front_end/core/models/live_score.dart';
import 'package:front_end/core/models/prediction.dart';
import 'package:front_end/core/theme/app_colors.dart';
import 'package:front_end/core/widgets/game_row.dart';
import 'package:front_end/core/widgets/pick_labels.dart';
import 'package:front_end/core/widgets/prediction_computing_retry.dart';
import 'package:front_end/core/widgets/win_probability_bar.dart';
import 'package:front_end/static/confidence_tiers.dart';

// Locates a Text widget rendering exactly `text` in exactly `color` --
// the actual/predicted score pair render as separate Text widgets
// distinguished only by color (ink for live/actual, cyan for predicted),
// not by a "(N)" parenthetical, so a plain find.text can't tell them
// apart on its own.
Finder _textStyled(String text, Color color) => find.byWidgetPredicate(
      (widget) => widget is Text && widget.data == text && widget.style?.color == color,
    );

// '12' = KC (home), '13' = LV (away) -- see nfl_team_colors.dart.
SportEvent _scheduledEvent({String? venueName, String? venueCity, String? venueState}) => SportEvent(
      eventId: '401547417',
      eventDate: '2026-09-14',
      kickoffTime: '2026-09-14T17:00:00Z',
      status: 'scheduled',
      week: 2,
      round: null,
      participants: const [
        Participant(entityId: '12', role: 'home', result: null),
        Participant(entityId: '13', role: 'away', result: null),
      ],
      predictionComparison: null,
      leadersComparison: null,
      venueName: venueName,
      venueCity: venueCity,
      venueState: venueState,
    );

SportEvent _completedEvent() => SportEvent(
      eventId: '401547419',
      eventDate: '2026-09-14',
      kickoffTime: '2026-09-14T17:00:00Z',
      status: 'completed',
      week: 2,
      round: null,
      participants: const [
        Participant(entityId: '12', role: 'home', result: ParticipantResult(score: 31, won: true)),
        Participant(entityId: '13', role: 'away', result: ParticipantResult(score: 17, won: false)),
      ],
      predictionComparison: const PredictionComparison(
        predictedHomeWinProbability: 0.67,
        predictedHomeWon: true,
        actualHomeWon: true,
        correct: true,
        predictedMargin: 10.0,
        actualMargin: 14,
        predictedHomeScore: 27.4,
        predictedAwayScore: 17.1,
        actualHomeScore: 31,
        actualAwayScore: 17,
      ),
      leadersComparison: null,
    );

const _prediction = EventPrediction(
  homeWinProbability: 0.68,
  homeWinProbabilityModelVersion: 3,
  margin: 6.5,
  homeScore: 27.4,
  awayScore: 20.9,
  leaders: null,
);

/// One row at `width`, with the standard prediction unless the event is completed.
Future<void> _pumpRow(
  WidgetTester tester,
  SportEvent event, {
  LiveEventState? liveState,
  double width = 800,
  double textScale = 1,
  String sport = 'nfl',
}) async {
  await tester.pumpWidget(ProviderScope(
    overrides: [eventPredictionProvider.overrideWith((ref, query) async => _prediction)],
    child: MaterialApp(
      home: MediaQuery(
        data: MediaQueryData(textScaler: TextScaler.linear(textScale)),
        child: Scaffold(
          body: SingleChildScrollView(
            child: SizedBox(width: width, child: GameRow(sport: sport, event: event, liveState: liveState)),
          ),
        ),
      ),
    ),
  ));
  await tester.pumpAndSettle();
}

SportEvent _completedWith(PredictionComparison? comparison) {
  final event = _completedEvent();
  return SportEvent(
    eventId: event.eventId,
    eventDate: event.eventDate,
    kickoffTime: event.kickoffTime,
    status: event.status,
    week: event.week,
    round: null,
    participants: event.participants,
    predictionComparison: comparison,
    leadersComparison: null,
  );
}

void main() {
  group('GameRow on a wide screen', () {
    testWidgets('an upcoming game shows the model scores, each side\'s win chance over the bar, and the line', (tester) async {
      await _pumpRow(tester, _scheduledEvent(venueName: 'Arrowhead Stadium', venueCity: 'Kansas City', venueState: 'MO'));

      expect(find.text('@'), findsNothing);
      expect(_textStyled('27', AppColors.cyan), findsOneWidget);
      expect(_textStyled('21', AppColors.cyan), findsOneWidget);
      // KC is home and favoured, so its end of the bar is the cyan one.
      expect(_textStyled('LV 32%', AppColors.inkSub), findsOneWidget);
      expect(_textStyled('KC 68%', AppColors.cyan), findsOneWidget);
      expect(find.byType(WinProbabilityBar), findsOneWidget);
      expect(_textStyled('KC -6.5', AppColors.cyan), findsOneWidget);
      expect(find.text(ConfidenceTier.forProbability(0.68).label), findsOneWidget);
      // The venue is on the game page only.
      expect(find.textContaining('Arrowhead'), findsNothing);
    });

    testWidgets('a live game keeps the bar and the line, and puts the clock under the LIVE pill', (tester) async {
      await _pumpRow(
        tester, _scheduledEvent(), sport: 'ncaafb',
        liveState: const LiveEventState(live: true, detail: 'Q1 4:00', homeScore: 10, awayScore: 3),
      );

      // The leader's score is white, the trailing side's dimmed; the model
      // stays beside both in cyan.
      expect(_textStyled('10', AppColors.ink), findsOneWidget);
      expect(_textStyled('3', AppColors.inkSub), findsOneWidget);
      expect(_textStyled('27', AppColors.cyan), findsOneWidget);
      expect(_textStyled('21', AppColors.cyan), findsOneWidget);
      expect(find.text('LIVE'), findsOneWidget);
      expect(tester.getTopLeft(find.text('Q1 4:00')).dy, greaterThan(tester.getCenter(find.text('LIVE')).dy));
      expect(find.byType(WinProbabilityBar), findsOneWidget);
      expect(find.textContaining('-6.5'), findsOneWidget);
    });

    testWidgets('a game ESPN reports over reads FINAL with its final score before storage catches up', (tester) async {
      await _pumpRow(
        tester, _scheduledEvent(),
        liveState: const LiveEventState(live: false, completed: true, detail: 'Final', homeScore: 31, awayScore: 17),
      );

      expect(find.text('FINAL'), findsOneWidget);
      expect(find.text('Final'), findsNothing);
      expect(_textStyled('31', AppColors.ink), findsOneWidget);
      expect(_textStyled('17', AppColors.inkSub), findsOneWidget);
    });

    testWidgets('a completed game shows the verdict, the pick, and the model line against the final one', (tester) async {
      await _pumpRow(tester, _completedEvent());

      expect(find.text('FINAL'), findsOneWidget);
      expect(_textStyled('31', AppColors.ink), findsOneWidget);
      expect(_textStyled('17', AppColors.inkSub), findsOneWidget);
      expect(_textStyled('27', AppColors.cyan), findsOneWidget);
      expect(_textStyled('17', AppColors.cyan), findsOneWidget);
      expect(_textStyled('KC -10.0', AppColors.cyan), findsOneWidget);
      expect(_textStyled('KC -14', AppColors.ink), findsOneWidget);
      expect(find.byType(MarginTrack), findsOneWidget);
      expect(find.text(VerdictPill.hitLabel), findsOneWidget);
      expect(find.text('KC 67%'), findsOneWidget);
      expect(find.byType(WinProbabilityBar), findsNothing);
    });

    testWidgets('a wrong pick reads MISS and names the side that was picked', (tester) async {
      await _pumpRow(tester, _completedWith(const PredictionComparison(
        predictedHomeWinProbability: 0.4, predictedHomeWon: false, actualHomeWon: true, correct: false,
        predictedMargin: -3.0, actualMargin: 14, predictedHomeScore: 20, predictedAwayScore: 23,
        actualHomeScore: 31, actualAwayScore: 17,
      )));

      expect(find.text(VerdictPill.missLabel), findsOneWidget);
      expect(find.text('LV 60%'), findsOneWidget);
      expect(_textStyled('LV -3.0', AppColors.cyan), findsOneWidget);
    });

    testWidgets('a completed game with no predicted score or margin keeps its slots', (tester) async {
      await _pumpRow(tester, _completedWith(const PredictionComparison(
        predictedHomeWinProbability: 0.67, predictedHomeWon: true, actualHomeWon: true, correct: true,
        predictedMargin: null, actualMargin: 14, predictedHomeScore: null, predictedAwayScore: null,
        actualHomeScore: 31, actualAwayScore: 17,
      )));

      expect(_textStyled('--', AppColors.cyan), findsNWidgets(2));
      // The pick alone, with no margin to print.
      expect(_textStyled('KC', AppColors.cyan), findsOneWidget);
    });

    testWidgets('a completed game with no recorded prediction says so', (tester) async {
      await _pumpRow(tester, _completedWith(null));

      expect(find.text('No prediction recorded'), findsOneWidget);
      expect(find.byType(VerdictPill), findsNothing);
    });
  });

  group('GameRow on a phone', () {
    for (final (width, textScale) in [(360.0, 1.0), (320.0, 1.3)]) {
      testWidgets('an upcoming card spells out confidence and fits at $width px, text x$textScale', (tester) async {
        await _pumpRow(tester, _scheduledEvent(), width: width, textScale: textScale);

        expect(tester.takeException(), isNull);
        // The tier is written out, never a bare dot with a tooltip.
        expect(find.text(ConfidenceTier.forProbability(0.68).label), findsOneWidget);
        expect(find.text('68%'), findsOneWidget);
        expect(find.text('32%'), findsOneWidget);
        expect(_textStyled('KC -6.5', AppColors.cyan), findsOneWidget);
        expect(find.byType(WinProbabilityBar), findsOneWidget);
      });

      testWidgets('a live card shows LIVE, the clock and the line at $width px, text x$textScale', (tester) async {
        await _pumpRow(
          tester, _scheduledEvent(), width: width, textScale: textScale,
          liveState: const LiveEventState(live: true, detail: 'End of 2nd Quarter', homeScore: 110, awayScore: 103),
        );

        expect(tester.takeException(), isNull);
        expect(find.text('LIVE'), findsOneWidget);
        expect(find.text('End of 2nd Quarter'), findsOneWidget);
        expect(_textStyled('KC -6.5', AppColors.cyan), findsOneWidget);
        expect(find.byType(WinProbabilityBar), findsNothing);
      });

      testWidgets('a completed card shows the verdict and both lines at $width px, text x$textScale', (tester) async {
        await _pumpRow(tester, _completedEvent(), width: width, textScale: textScale);

        expect(tester.takeException(), isNull);
        expect(find.text(VerdictPill.hitLabel), findsOneWidget);
        expect(_textStyled('KC -14', AppColors.ink), findsOneWidget);
        expect(_textStyled('KC -10.0', AppColors.cyan), findsOneWidget);
        // The chance shows on the picked side only.
        expect(find.text('67%'), findsOneWidget);
        expect(find.text('33%'), findsNothing);
      });
    }
  });

  group('GameRow states', () {
    Widget row(SportEvent event, {Future<EventPrediction> Function()? prediction, double? width}) => ProviderScope(
          retry: (retryCount, error) => null,
          overrides: [
            if (prediction != null) eventPredictionProvider.overrideWith((ref, query) => prediction()),
          ],
          child: MaterialApp(
            home: Scaffold(body: SizedBox(width: width ?? 800, child: GameRow(sport: 'nfl', event: event))),
          ),
        );

    testWidgets('a prediction load error shows --', (tester) async {
      await tester.pumpWidget(row(_scheduledEvent(), prediction: () async => throw Exception('bad')));
      await tester.pumpAndSettle();

      expect(find.text('--'), findsWidgets);
    });

    testWidgets('a still-computing prediction shows the compact retry', (tester) async {
      await tester.pumpWidget(row(_scheduledEvent(), prediction: () async => throw const PredictionComputingException(600)));
      await tester.pump();
      await tester.pump();

      expect(find.byType(PredictionComputingRetry), findsOneWidget);
      await tester.pump(const Duration(minutes: 20));
      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('a stale prediction re-fetches once its freshness retry fires', (tester) async {
      var calls = 0;
      await tester.pumpWidget(row(_scheduledEvent(), prediction: () async {
        calls++;
        return const EventPrediction(
          homeWinProbability: 0.68, homeWinProbabilityModelVersion: 3, margin: 6.5, homeScore: 27.4, awayScore: 20.9,
          leaders: null, stale: true, staleRetryAfterSeconds: 60,
        );
      }));
      await tester.pump();
      await tester.pump();

      await tester.pump(const Duration(minutes: 5));
      await tester.pump();

      expect(calls, greaterThan(1));
      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('a completed event with no recorded prediction says so', (tester) async {
      final event = _completedEvent();
      final noComparison = SportEvent(
        eventId: event.eventId,
        eventDate: event.eventDate,
        kickoffTime: event.kickoffTime,
        status: event.status,
        week: event.week,
        round: null,
        participants: event.participants,
        predictionComparison: null,
        leadersComparison: null,
      );
      await tester.pumpWidget(row(noComparison));
      await tester.pumpAndSettle();

      expect(find.text('No prediction recorded'), findsOneWidget);
    });

    testWidgets('tapping the row navigates to the event detail route', (tester) async {
      final router = GoRouter(routes: [
        GoRoute(path: '/', builder: (_, __) => Scaffold(body: GameRow(sport: 'nfl', event: _completedEvent()))),
        GoRoute(path: '/nfl/events/:id', builder: (_, state) => Text('detail ${state.pathParameters['id']}')),
      ]);
      await tester.pumpWidget(ProviderScope(child: MaterialApp.router(routerConfig: router)));
      await tester.pumpAndSettle();

      await tester.tap(find.byType(GameRow));
      await tester.pumpAndSettle();

      expect(find.text('detail 401547419'), findsOneWidget);
    });
  });

  group('timezoneLabelFor', () {
    test('names a US zone, standard or daylight', () {
      expect(timezoneLabelFor(const Duration(hours: -5), const Duration(hours: -5), const Duration(hours: -4)), 'EST');
      expect(timezoneLabelFor(const Duration(hours: -4), const Duration(hours: -5), const Duration(hours: -4)), 'EDT');
    });

    test('falls back to UTC+/-N outside the US', () {
      expect(timezoneLabelFor(const Duration(hours: 2), const Duration(hours: 1), const Duration(hours: 2)), 'UTC+2');
      expect(timezoneLabelFor(const Duration(hours: -3), const Duration(hours: -3), const Duration(hours: -3)), 'UTC-3');
    });
  });
}
