import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/events_repository.dart';
import 'package:front_end/core/data/live_scores_repository.dart';
import 'package:front_end/core/models/event.dart';
import 'package:front_end/core/models/live_score.dart';
import 'package:front_end/core/models/prediction.dart';
import 'package:front_end/features/events/event_detail_page.dart';

const _prediction = EventPrediction(
  homeWinProbability: 0.62,
  homeWinProbabilityModelVersion: 9,
  margin: 4.5,
  homeScore: 27.3,
  awayScore: 22.8,
  leaders: null,
);

SportEvent _scheduledEvent(String kickoff) => SportEvent(
      eventId: '401547417',
      eventDate: kickoff.split('T').first,
      kickoffTime: kickoff,
      status: 'scheduled',
      week: 2,
      round: null,
      participants: const [
        Participant(entityId: '12', role: 'home', result: null),
        Participant(entityId: '13', role: 'away', result: null),
      ],
      predictionComparison: null,
      leadersComparison: null,
    );

void main() {
  testWidgets('polls live scores every 30s for a scheduled event', (tester) async {
    var liveScoreCalls = 0;

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          eventsListProvider.overrideWith((ref, query) async => query.status == 'scheduled' ? [_scheduledEvent('2026-09-14T17:00:00Z')] : []),
          eventPredictionProvider.overrideWith((ref, query) async => _prediction),
          liveScoresProvider.overrideWith((ref, sport) async {
            liveScoreCalls++;
            return const <String, LiveEventState>{};
          }),
        ],
        child: const MaterialApp(home: Scaffold(body: EventDetailPage(sportId: 'nfl', eventId: '401547417'))),
      ),
    );
    await tester.pumpAndSettle();
    final initialCalls = liveScoreCalls;

    await tester.pump(const Duration(seconds: 31));
    await tester.pumpAndSettle();

    expect(liveScoreCalls, greaterThan(initialCalls));
  });

  testWidgets('does not poll the prediction for a kickoff far in the future', (tester) async {
    var predictionCalls = 0;

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          // 30 days out -- well outside the near-kickoff poll window.
          eventsListProvider.overrideWith(
            (ref, query) async => query.status == 'scheduled'
                ? [_scheduledEvent(DateTime.now().toUtc().add(const Duration(days: 30)).toIso8601String())]
                : [],
          ),
          eventPredictionProvider.overrideWith((ref, query) async {
            predictionCalls++;
            return _prediction;
          }),
          liveScoresProvider.overrideWith((ref, sport) async => const <String, LiveEventState>{}),
        ],
        child: const MaterialApp(home: Scaffold(body: EventDetailPage(sportId: 'nfl', eventId: '401547417'))),
      ),
    );
    await tester.pumpAndSettle();
    final initialCalls = predictionCalls;

    await tester.pump(const Duration(seconds: 31));
    await tester.pumpAndSettle();

    expect(predictionCalls, initialCalls);
  });

  testWidgets('polls the prediction near kickoff', (tester) async {
    var predictionCalls = 0;

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          eventsListProvider.overrideWith(
            (ref, query) async => query.status == 'scheduled'
                ? [_scheduledEvent(DateTime.now().toUtc().add(const Duration(minutes: 5)).toIso8601String())]
                : [],
          ),
          eventPredictionProvider.overrideWith((ref, query) async {
            predictionCalls++;
            return _prediction;
          }),
          liveScoresProvider.overrideWith((ref, sport) async => const <String, LiveEventState>{}),
        ],
        child: const MaterialApp(home: Scaffold(body: EventDetailPage(sportId: 'nfl', eventId: '401547417'))),
      ),
    );
    await tester.pumpAndSettle();
    final initialCalls = predictionCalls;

    await tester.pump(const Duration(seconds: 31));
    await tester.pumpAndSettle();

    expect(predictionCalls, greaterThan(initialCalls));
  });
}
