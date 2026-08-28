import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/event.dart';
import 'package:front_end/core/widgets/matchup_hero.dart';

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
      predictionComparison: null,
      leadersComparison: null,
    );

void main() {
  testWidgets('MatchupResultHero shows -- for a missing predicted margin instead of dropping the trio', (tester) async {
    const comparison = PredictionComparison(
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
    );

    await tester.pumpWidget(MaterialApp(
      home: Scaffold(body: MatchupResultHero(sport: 'nfl', event: _completedEvent(), comparison: comparison)),
    ));

    expect(find.text('PRED MARGIN'), findsOneWidget);
    expect(find.text('--'), findsOneWidget);
  });
}
