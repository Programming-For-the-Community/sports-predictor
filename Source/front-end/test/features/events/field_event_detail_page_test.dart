import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/field_events_repository.dart';
import 'package:front_end/core/data/live_scores_repository.dart';
import 'package:front_end/core/models/field_live_score.dart';
import 'package:front_end/core/models/field_prediction.dart';
import 'package:front_end/core/widgets/prediction_freshness_badge.dart';
import 'package:front_end/features/events/field_event_detail_page.dart';

Map<String, dynamic> _fieldResponse() => {
      'sport': 'pga', 'event_id': '401811963', 'event_type': 'field',
      'tournament_name': 'BMW Championship', 'status': 'scheduled',
      'cutline': {
        'projected_cut_score': {'value': -2.0, 'model_version': 3},
      },
      'field': [
        {
          'entity_id': '10140', 'name': 'Xander Schauffele', 'country': 'USA',
          'predictions': {
            'top_10_probability': {'value': 0.42, 'model_version': 1},
            'projected_score_to_par': {'value': -8.5, 'model_version': 2},
          },
        },
      ],
    };

Map<String, dynamic> _twoSidedResponse() => {
      'sport': 'pga', 'event_id': '401465497-match-10951', 'event_type': 'match_play',
      'tournament_name': 'Presidents Cup', 'status': 'scheduled',
      'match_format': 'Foursomes', 'session_name': 'Session 1',
      'home': {'entity_id': '1', 'name': 'United States'},
      'away': {'entity_id': '3', 'name': 'International'},
      'predictions': {
        'match_win_probability': {'value': 0.58, 'model_version': 1},
      },
    };

void main() {
  testWidgets('a field event renders the leaderboard table', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          fieldEventPredictionProvider.overrideWith((ref, query) async => parsePgaEventPrediction(_fieldResponse())),
          fieldLiveScoresProvider.overrideWith((ref, sport) async => const <String, FieldLiveEventState>{}),
        ],
        child: const MaterialApp(home: Scaffold(body: FieldEventDetailPage(sportId: 'pga', eventId: '401811963'))),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('BMW Championship'), findsOneWidget);
    expect(find.text('Xander Schauffele'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('a match_play event renders the two-sided matchup view, not a leaderboard', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          fieldEventPredictionProvider.overrideWith((ref, query) async => parsePgaEventPrediction(_twoSidedResponse())),
          fieldLiveScoresProvider.overrideWith((ref, sport) async => const <String, FieldLiveEventState>{}),
        ],
        child: const MaterialApp(home: Scaffold(body: FieldEventDetailPage(sportId: 'pga', eventId: '401465497-match-10951'))),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('United States'), findsOneWidget);
    expect(find.text('International'), findsOneWidget);
    expect(find.textContaining('58%'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('a match_play event overlays live margin data onto the matchup view', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          fieldEventPredictionProvider.overrideWith((ref, query) async => parsePgaEventPrediction(_twoSidedResponse())),
          fieldLiveScoresProvider.overrideWith((ref, sport) async => const <String, FieldLiveEventState>{}),
          pgaLiveScoresProvider.overrideWith((ref, sport) async => {
                '401465497-match-10951': parsePgaLiveEventState({
                  'event_type': 'match_play', 'status': 'scheduled', 'tournament_name': 'Presidents Cup',
                  'participants': {
                    '1': {'status': 'finished', 'won': true, 'halved': false, 'margin_display': '6 & 5', 'margin_holes': 6.0},
                    '3': {'status': 'finished', 'won': false, 'halved': false},
                  },
                }),
              }),
        ],
        child: const MaterialApp(home: Scaffold(body: FieldEventDetailPage(sportId: 'pga', eventId: '401465497-match-10951'))),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('6 & 5'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('polls the prediction and live scores every 60s', (tester) async {
    var predictionCalls = 0;
    var liveScoreCalls = 0;

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          fieldEventPredictionProvider.overrideWith((ref, query) async {
            predictionCalls++;
            return parsePgaEventPrediction(_fieldResponse());
          }),
          fieldLiveScoresProvider.overrideWith((ref, sport) async {
            liveScoreCalls++;
            return const <String, FieldLiveEventState>{};
          }),
        ],
        child: const MaterialApp(home: Scaffold(body: FieldEventDetailPage(sportId: 'pga', eventId: '401811963'))),
      ),
    );
    await tester.pumpAndSettle();
    final initialPredictionCalls = predictionCalls;
    final initialLiveScoreCalls = liveScoreCalls;

    await tester.pump(const Duration(seconds: 61));
    await tester.pumpAndSettle();

    expect(predictionCalls, greaterThan(initialPredictionCalls));
    expect(liveScoreCalls, greaterThan(initialLiveScoreCalls));
  });

  testWidgets('a cold-cache-miss keeps retrying until the compute resolves', (tester) async {
    var predictionCalls = 0;

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          fieldEventPredictionProvider.overrideWith((ref, query) async {
            predictionCalls++;
            if (predictionCalls < 3) throw const PredictionComputingException(1);
            return parsePgaEventPrediction(_fieldResponse());
          }),
          fieldLiveScoresProvider.overrideWith((ref, sport) async => const <String, FieldLiveEventState>{}),
        ],
        child: const MaterialApp(home: Scaffold(body: FieldEventDetailPage(sportId: 'pga', eventId: '401811963'))),
      ),
    );
    await tester.pumpAndSettle();

    expect(predictionCalls, 3);
    expect(find.text('Computing prediction...'), findsNothing);
  });

  Widget page(Future<PgaEventPrediction> Function() prediction, {String eventId = '401811963'}) => ProviderScope(
        retry: (retryCount, error) => null,
        overrides: [
          fieldEventPredictionProvider.overrideWith((ref, query) => prediction()),
          fieldLiveScoresProvider.overrideWith((ref, sport) async => const <String, FieldLiveEventState>{}),
        ],
        child: MaterialApp(home: Scaffold(body: FieldEventDetailPage(sportId: 'pga', eventId: eventId))),
      );

  testWidgets('shows the computing retry while the prediction is still computing', (tester) async {
    await tester.pumpWidget(page(() async => throw const PredictionComputingException(600)));
    await tester.pump();
    await tester.pump();

    expect(find.text('Computing prediction...'), findsOneWidget);
    await tester.pump(const Duration(minutes: 20));
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('a stale field prediction shows the freshness badge', (tester) async {
    await tester.pumpWidget(page(() async => parsePgaEventPrediction({..._fieldResponse(), 'stale': true, 'retry_after_seconds': 600})));
    await tester.pump();
    await tester.pump();

    expect(find.byType(PredictionFreshnessBadge), findsOneWidget);
    await tester.pump(const Duration(minutes: 20));
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('a stale two-sided prediction shows the freshness badge', (tester) async {
    await tester.pumpWidget(page(
      () async => parsePgaEventPrediction({..._twoSidedResponse(), 'stale': true, 'retry_after_seconds': 600}),
      eventId: '401465497-match-10951',
    ));
    await tester.pump();
    await tester.pump();

    expect(find.byType(PredictionFreshnessBadge), findsOneWidget);
    await tester.pump(const Duration(minutes: 20));
    await tester.pumpWidget(const SizedBox());
  });
}
