import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/events_repository.dart';
import 'package:front_end/core/data/live_scores_repository.dart';
import 'package:front_end/core/models/event.dart';
import 'package:front_end/core/models/event_leaders.dart';
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

const _predictionWithLeaders = EventPrediction(
  homeWinProbability: 0.62,
  homeWinProbabilityModelVersion: 9,
  margin: 4.5,
  homeScore: 27.3,
  awayScore: 22.8,
  leaders: EventLeaders(
    home: TeamLeaders({
      'passing': [PlayerStatLine(entityId: '100', name: 'QB One', stats: {'passing_yards': 250})],
      'receiving': [],
      'rushing': [],
      'sacks': [],
    }),
    away: TeamLeaders({'passing': [], 'receiving': [], 'rushing': [], 'sacks': []}),
  ),
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

  testWidgets('a live event shows predicted-vs-live-so-far leaders, not the predicted-only panel', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          eventsListProvider.overrideWith((ref, query) async => query.status == 'scheduled' ? [_scheduledEvent('2026-09-14T17:00:00Z')] : []),
          eventPredictionProvider.overrideWith((ref, query) async => _predictionWithLeaders),
          liveScoresProvider.overrideWith((ref, sport) async => const {
                '401547417': LiveEventState(
                  live: true,
                  detail: 'Q2 04:12',
                  homeScore: 10,
                  awayScore: 7,
                  playerStats: {
                    '100': {'passing_yards': 140},
                  },
                ),
              }),
        ],
        child: const MaterialApp(home: Scaffold(body: EventDetailPage(sportId: 'nfl', eventId: '401547417'))),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('PLAYER LEADERS -- LIVE'), findsOneWidget);
    expect(find.text('PLAYER LEADERS'), findsNothing);
    // Predicted 250 shown alongside the live-so-far 140, the same
    // ink-actual/cyan-predicted row (no "(pred N)" wording)
    // TeamLeadersComparisonPanel renders for a completed event.
    expect(find.textContaining('140'), findsOneWidget);
    expect(find.textContaining('250'), findsOneWidget);
    expect(find.textContaining('pred'), findsNothing);
  });

  testWidgets('a scheduled (not yet live) event with predicted leaders still shows the predicted-only panel', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          eventsListProvider.overrideWith((ref, query) async => query.status == 'scheduled' ? [_scheduledEvent('2026-09-14T17:00:00Z')] : []),
          eventPredictionProvider.overrideWith((ref, query) async => _predictionWithLeaders),
          liveScoresProvider.overrideWith((ref, sport) async => const <String, LiveEventState>{}),
        ],
        child: const MaterialApp(home: Scaffold(body: EventDetailPage(sportId: 'nfl', eventId: '401547417'))),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('PLAYER LEADERS'), findsOneWidget);
    expect(find.text('PLAYER LEADERS -- LIVE'), findsNothing);
  });

  testWidgets('a cold-cache-miss keeps retrying past a single retryAfterSeconds until the compute resolves', (tester) async {
    var predictionCalls = 0;

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          eventsListProvider.overrideWith((ref, query) async => query.status == 'scheduled' ? [_scheduledEvent('2026-09-14T17:00:00Z')] : []),
          eventPredictionProvider.overrideWith((ref, query) async {
            predictionCalls++;
            // Resolves on the 3rd attempt, past what a single retry would
            // cover (see PredictionComputingRetry's own doc comment).
            // pumpAndSettle fast-forwards through pending timers, so this
            // only asserts the end state: stuck at predictionCalls == 2
            // would mean the retry failed to reschedule after a failed
            // attempt.
            if (predictionCalls < 3) throw const PredictionComputingException(1);
            return _prediction;
          }),
          liveScoresProvider.overrideWith((ref, sport) async => const <String, LiveEventState>{}),
        ],
        child: const MaterialApp(home: Scaffold(body: EventDetailPage(sportId: 'nfl', eventId: '401547417'))),
      ),
    );
    await tester.pumpAndSettle();

    expect(predictionCalls, 3);
    expect(find.text('Computing prediction...'), findsNothing);
  });
}
