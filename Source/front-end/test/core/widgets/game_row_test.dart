import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/events_repository.dart';
import 'package:front_end/core/models/event.dart';
import 'package:front_end/core/models/live_score.dart';
import 'package:front_end/core/models/prediction.dart';
import 'package:front_end/core/theme/app_colors.dart';
import 'package:front_end/core/widgets/game_row.dart';
import 'package:front_end/core/widgets/win_probability_bar.dart';

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

void main() {
  group('GameRow', () {
    testWidgets('scheduled row has no @ sign and shows the predicted score next to the actual one', (tester) async {
      await tester.pumpWidget(ProviderScope(
        overrides: [
          eventPredictionProvider.overrideWith(
            (ref, query) async => const EventPrediction(
              homeWinProbability: 0.68,
              homeWinProbabilityModelVersion: 3,
              margin: 6.5,
              homeScore: 27.4,
              awayScore: 20.9,
              leaders: null,
            ),
          ),
        ],
        child: MaterialApp(home: Scaffold(body: GameRow(sport: 'nfl', event: _scheduledEvent()))),
      ));
      await tester.pumpAndSettle();

      expect(find.text('@'), findsNothing);
      // Predicted scores render in cyan next to each team's own line, no
      // actual score yet (pre-kickoff) so no ink-colored number to
      // collide with.
      expect(_textStyled('27', AppColors.cyan), findsOneWidget);
      expect(_textStyled('21', AppColors.cyan), findsOneWidget);
      // Percentage lives only on the right (_LivePredictionSummary).
      expect(find.textContaining('68%'), findsOneWidget);
    });

    testWidgets('a live event still shows the pre-game predicted score next to the live one', (tester) async {
      await tester.pumpWidget(ProviderScope(
        overrides: [
          eventPredictionProvider.overrideWith(
            (ref, query) async => const EventPrediction(
              homeWinProbability: 0.68,
              homeWinProbabilityModelVersion: 3,
              margin: 6.5,
              homeScore: 27.4,
              awayScore: 20.9,
              leaders: null,
            ),
          ),
        ],
        child: MaterialApp(
          home: Scaffold(
            body: GameRow(
              sport: 'ncaafb',
              event: _scheduledEvent(),
              liveState: const LiveEventState(live: true, detail: 'Q1 4:00', homeScore: 10, awayScore: 3),
            ),
          ),
        ),
      ));
      await tester.pumpAndSettle();

      // The live score (ink) and the still-fetched pre-game predicted
      // score (cyan) both render -- previously the predicted-score
      // fetch was skipped entirely once an event went live, leaving a
      // permanent '--' next to the live score.
      expect(_textStyled('10', AppColors.ink), findsOneWidget);
      expect(_textStyled('3', AppColors.ink), findsOneWidget);
      expect(_textStyled('27', AppColors.cyan), findsOneWidget);
      expect(_textStyled('21', AppColors.cyan), findsOneWidget);
      expect(find.text('Q1 4:00'), findsOneWidget);
    });

    testWidgets(
        'a live event keeps the pick/margin/confidence summary, with the LIVE pill/clock taking the pre-game '
        'win-probability bar\'s own slot on the same row rather than staggering onto a separate line', (tester) async {
      // A wide desktop viewport -- SizedBox(width: ...) alone gets
      // clamped to the default 800x600 test surface, silently making
      // every width variant identical, so the surface itself has to be
      // resized to actually exercise the extra room a real wide window
      // gives the predictionArea column.
      await tester.binding.setSurfaceSize(const Size(1800, 800));
      tester.view.physicalSize = const Size(1800, 800);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      await tester.pumpWidget(ProviderScope(
        overrides: [
          eventPredictionProvider.overrideWith(
            (ref, query) async => const EventPrediction(
              homeWinProbability: 0.68,
              homeWinProbabilityModelVersion: 3,
              margin: 6.5,
              homeScore: 27.4,
              awayScore: 20.9,
              leaders: null,
            ),
          ),
        ],
        child: MaterialApp(
          home: Scaffold(
            body: GameRow(
              sport: 'nfl',
              event: _scheduledEvent(),
              liveState: const LiveEventState(live: true, detail: 'Q1 4:00', homeScore: 10, awayScore: 3),
            ),
          ),
        ),
      ));
      await tester.pumpAndSettle();

      // KC is home and favored (0.68 >= 0.5).
      expect(find.textContaining('KC -6.5'), findsOneWidget);
      expect(find.text('68%'), findsOneWidget);
      expect(find.text('HIGH'), findsOneWidget); // edge 0.18 -> HIGH tier
      // The pre-game win-probability bar is dropped once live -- the
      // LIVE pill already covers that state.
      expect(find.byType(WinProbabilityBar), findsNothing);

      // LIVE pill/clock and ConfidencePill sit on the same row -- no
      // more staggering across two separate lines.
      final liveY = tester.getCenter(find.text('Q1 4:00')).dy;
      final confidenceY = tester.getCenter(find.text('HIGH')).dy;
      expect(liveY, equals(confidenceY));

      // A real gap (not just the small fixed spacer) separates the LIVE
      // pill/clock from the pick/margin/confidence group -- the leftover
      // space lands between them (spaceBetween) instead of the two
      // blocks bunching up flush against each other on the right edge.
      final liveRightEdge = tester.getTopRight(find.text('Q1 4:00')).dx;
      final pickLeftEdge = tester.getTopLeft(find.text('68%')).dx;
      expect(pickLeftEdge - liveRightEdge, greaterThan(100));
    });

    testWidgets('a scheduled (not yet live) event still shows the win-probability bar', (tester) async {
      await tester.pumpWidget(ProviderScope(
        overrides: [
          eventPredictionProvider.overrideWith(
            (ref, query) async => const EventPrediction(
              homeWinProbability: 0.68,
              homeWinProbabilityModelVersion: 3,
              margin: 6.5,
              homeScore: 27.4,
              awayScore: 20.9,
              leaders: null,
            ),
          ),
        ],
        child: MaterialApp(home: Scaffold(body: GameRow(sport: 'nfl', event: _scheduledEvent()))),
      ));
      await tester.pumpAndSettle();

      expect(find.byType(WinProbabilityBar), findsOneWidget);
    });

    testWidgets('a completed event shows a FINAL status pill', (tester) async {
      await tester.pumpWidget(ProviderScope(
        child: MaterialApp(home: Scaffold(body: GameRow(sport: 'nfl', event: _completedEvent()))),
      ));
      await tester.pumpAndSettle();

      expect(find.text('FINAL'), findsOneWidget);
      expect(find.byType(WinProbabilityBar), findsNothing);
    });

    testWidgets('a narrow (mobile) completed event collapses the FINAL pill to just its dot', (tester) async {
      await tester.pumpWidget(ProviderScope(
        child: MaterialApp(home: Scaffold(body: SizedBox(width: 360, child: GameRow(sport: 'nfl', event: _completedEvent())))),
      ));
      await tester.pumpAndSettle();

      expect(find.text('FINAL'), findsNothing);
      expect(find.byTooltip('FINAL'), findsOneWidget);
    });

    testWidgets('a narrow (mobile) width collapses the confidence pill to just its dot', (tester) async {
      await tester.pumpWidget(ProviderScope(
        overrides: [
          eventPredictionProvider.overrideWith(
            (ref, query) async => const EventPrediction(
              homeWinProbability: 0.68,
              homeWinProbabilityModelVersion: 3,
              margin: 6.5,
              homeScore: 27.4,
              awayScore: 20.9,
              leaders: null,
            ),
          ),
        ],
        child: MaterialApp(
          home: Scaffold(body: SizedBox(width: 360, child: GameRow(sport: 'nfl', event: _scheduledEvent()))),
        ),
      ));
      await tester.pumpAndSettle();

      // edge = 0.18 -> HIGH tier, collapsed to just its colored dot below
      // the stack breakpoint -- the text label moves into a Tooltip.
      expect(find.text('HIGH'), findsNothing);
      expect(find.byTooltip('HIGH'), findsOneWidget);
    });

    testWidgets('a narrow (mobile) live event collapses the LIVE pill to just its dot', (tester) async {
      await tester.pumpWidget(ProviderScope(
        overrides: [
          eventPredictionProvider.overrideWith(
            (ref, query) async => const EventPrediction(
              homeWinProbability: 0.68,
              homeWinProbabilityModelVersion: 3,
              margin: 6.5,
              homeScore: 27.4,
              awayScore: 20.9,
              leaders: null,
            ),
          ),
        ],
        child: MaterialApp(
          home: Scaffold(
            body: SizedBox(
              width: 360,
              child: GameRow(
                sport: 'ncaafb',
                event: _scheduledEvent(),
                liveState: const LiveEventState(live: true, detail: 'Q1 4:00', homeScore: 10, awayScore: 3),
              ),
            ),
          ),
        ),
      ));
      await tester.pumpAndSettle();

      expect(find.text('LIVE'), findsNothing);
      expect(find.byTooltip('LIVE'), findsOneWidget);
    });

    testWidgets('venue label renders name and city/state between the matchup and the pick', (tester) async {
      await tester.pumpWidget(ProviderScope(
        overrides: [
          eventPredictionProvider.overrideWith(
            (ref, query) async => const EventPrediction(
              homeWinProbability: 0.5,
              homeWinProbabilityModelVersion: 3,
              margin: 0,
              homeScore: 21,
              awayScore: 21,
              leaders: null,
            ),
          ),
        ],
        child: MaterialApp(
          home: Scaffold(
            body: GameRow(
              sport: 'nfl',
              event: _scheduledEvent(venueName: 'Arrowhead Stadium', venueCity: 'Kansas City', venueState: 'MO'),
            ),
          ),
        ),
      ));
      await tester.pumpAndSettle();

      expect(find.textContaining('Arrowhead Stadium'), findsOneWidget);
      expect(find.byIcon(Icons.location_on_outlined), findsOneWidget);
      // No mention of the venue being indoor/outdoor -- location/name only.
      expect(find.textContaining('Indoor'), findsNothing);
      expect(find.textContaining('Outdoor'), findsNothing);
    });

    testWidgets('venue line renders nothing when the event has no venue data', (tester) async {
      await tester.pumpWidget(ProviderScope(
        overrides: [
          eventPredictionProvider.overrideWith(
            (ref, query) async => const EventPrediction(
              homeWinProbability: 0.5,
              homeWinProbabilityModelVersion: 3,
              margin: 0,
              homeScore: 21,
              awayScore: 21,
              leaders: null,
            ),
          ),
        ],
        child: MaterialApp(home: Scaffold(body: GameRow(sport: 'nfl', event: _scheduledEvent()))),
      ));
      await tester.pumpAndSettle();

      expect(find.byIcon(Icons.location_on_outlined), findsNothing);
    });

    testWidgets('completed row has no @ sign and does not repeat the score on the right', (tester) async {
      await tester.pumpWidget(ProviderScope(
        child: MaterialApp(home: Scaffold(body: GameRow(sport: 'nfl', event: _completedEvent()))),
      ));
      await tester.pumpAndSettle();

      expect(find.text('@'), findsNothing);
      // Predicted score shows next to each team's own actual score, so
      // the right-hand recap states the pick/margin/probability only,
      // not the same score pair a second time. Away's predicted (17,
      // cyan) and actual (17, ink) happen to coincide numerically --
      // color, not a parenthetical, is what tells them apart.
      expect(_textStyled('27', AppColors.cyan), findsOneWidget);
      expect(_textStyled('17', AppColors.cyan), findsOneWidget);
      expect(_textStyled('31', AppColors.ink), findsOneWidget);
      expect(_textStyled('17', AppColors.ink), findsOneWidget);
      expect(find.textContaining('Predicted KC (67%) by 10.0'), findsOneWidget);
      expect(find.textContaining('31-17'), findsNothing);
    });

    testWidgets('a completed event with no predicted score shows -- instead of hiding the slot', (tester) async {
      final event = SportEvent(
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
          predictedMargin: null,
          actualMargin: 14,
          predictedHomeScore: null,
          predictedAwayScore: null,
          actualHomeScore: 31,
          actualAwayScore: 17,
        ),
        leadersComparison: null,
      );

      await tester.pumpWidget(ProviderScope(
        child: MaterialApp(home: Scaffold(body: GameRow(sport: 'nfl', event: event))),
      ));
      await tester.pumpAndSettle();

      expect(_textStyled('--', AppColors.cyan), findsNWidgets(2));
    });
  });
}
