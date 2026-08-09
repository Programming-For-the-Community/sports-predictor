import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/events_repository.dart';
import 'package:front_end/core/models/event.dart';
import 'package:front_end/core/models/prediction.dart';
import 'package:front_end/core/widgets/game_row.dart';

// '12' = KC (home), '13' = LV (away) -- see nfl_team_colors.dart, matching
// the convention event_list_page_mobile_test.dart already established.
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
      // Predicted scores now render in parens next to each team's own
      // line, not only inside the right-hand pick/margin text.
      expect(find.textContaining('(27)'), findsOneWidget);
      expect(find.textContaining('(21)'), findsOneWidget);
      // Percentage now lives only on the right (_LivePredictionSummary).
      expect(find.textContaining('68%'), findsOneWidget);
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
      // Predicted score now shows next to each team's own actual score...
      expect(find.textContaining('(27)'), findsOneWidget);
      expect(find.textContaining('(17)'), findsOneWidget);
      // ...so the right-hand recap states the pick/margin/probability only,
      // not the same score pair a second time.
      expect(find.textContaining('Predicted KC (67%) by 10.0'), findsOneWidget);
      expect(find.textContaining('31-17'), findsNothing);
    });
  });
}
