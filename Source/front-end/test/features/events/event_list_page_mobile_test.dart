import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/events_repository.dart';
import 'package:front_end/core/data/live_scores_repository.dart';
import 'package:front_end/core/models/event.dart';
import 'package:front_end/core/models/live_score.dart';
import 'package:front_end/core/models/prediction.dart';
import 'package:front_end/features/events/event_list_page.dart';

import '../../support/mobile_viewport.dart';

SportEvent _scheduledEvent(String id, String kickoff, {String? round, int? week}) => SportEvent(
      eventId: id,
      eventDate: kickoff.split('T').first,
      kickoffTime: kickoff,
      status: 'scheduled',
      week: week,
      round: round,
      participants: const [
        Participant(entityId: '12', role: 'home', result: null),
        Participant(entityId: '13', role: 'away', result: null),
      ],
      predictionComparison: null,
      leadersComparison: null,
    );

SportEvent _completedEvent(String id, String kickoff) => SportEvent(
      eventId: id,
      eventDate: kickoff.split('T').first,
      kickoffTime: kickoff,
      status: 'completed',
      week: 2,
      round: null,
      participants: const [
        Participant(entityId: '12', role: 'home', result: ParticipantResult(score: 31, won: true)),
        Participant(entityId: '13', role: 'away', result: ParticipantResult(score: 17, won: false)),
      ],
      // Away favored (predictedHomeWinProbability < 0.5) and double-digit
      // everything -- the shape most likely to overflow the "Predicted
      // AWY (67%) by 13.5 (31-17)" line's fixed width.
      predictionComparison: const PredictionComparison(
        predictedHomeWinProbability: 0.33,
        predictedHomeWon: false,
        actualHomeWon: true,
        correct: false,
        predictedMargin: 13.5,
        actualMargin: 14,
        predictedHomeScore: 17.9,
        predictedAwayScore: 31.4,
        actualHomeScore: 31,
        actualAwayScore: 17,
      ),
      leadersComparison: null,
    );

void main() {
  for (final width in mobileViewportWidths) {
    testWidgets('renders with no overflow at ${width}px wide', (tester) async {
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [
            eventsListProvider.overrideWith(
              (ref, query) async => query.status == 'completed'
                  ? [_completedEvent('401547419', '2026-09-14T17:00:00Z')]
                  : [
                      _scheduledEvent('401547417', '2026-09-14T17:00:00Z', week: 2),
                      _scheduledEvent('401547418', '2026-09-14T20:25:00Z', round: 'Divisional'),
                    ],
            ),
            // One event live -- exercises _LiveStatus's pill + game-clock
            // text (game_row.dart), not just the pre-game/prediction path
            // the other mobile tests already cover.
            liveScoresProvider.overrideWith(
              (ref, sport) async => {
                '401547417': const LiveEventState(live: true, detail: 'Q3 08:14', homeScore: 17, awayScore: 14),
              },
            ),
            // Double-digit margin/scores -- catches a real overflow
            // (ConfidencePill pushed off-card) that shorter values didn't.
            eventPredictionProvider.overrideWith(
              (ref, query) async => const EventPrediction(
                homeWinProbability: 0.73,
                homeWinProbabilityModelVersion: 9,
                margin: 13.5,
                homeScore: 31.4,
                awayScore: 17.9,
                leaders: null,
              ),
            ),
          ],
          child: const MaterialApp(home: Scaffold(body: EventListPage(sportId: 'nfl'))),
        ),
      );
      expect(tester.takeException(), isNull);

      // _ComparisonSummary (game_row.dart) is only reached on this tab --
      // never exercised at all before the predicted-win-percentage text
      // was added to its own line.
      await tester.tap(find.text('Completed'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    });
  }
}
