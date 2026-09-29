import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/f1_events_repository.dart';
import 'package:front_end/core/data/live_scores_repository.dart';
import 'package:front_end/core/models/f1_live_score.dart';
import 'package:front_end/core/models/f1_prediction.dart';
import 'package:front_end/core/widgets/prediction_freshness_badge.dart';
import 'package:front_end/features/events/f1_event_detail_page.dart';

F1EventPrediction _fieldPrediction({List<F1ConstructorPrediction> constructors = const []}) => F1EventPrediction(
      eventId: '2026-5',
      eventType: 'field',
      raceName: 'Monaco Grand Prix',
      field: [F1DriverPrediction(entityId: 'max_verstappen', name: 'Max Verstappen')],
      constructors: constructors,
    );

Widget _wrap(F1EventPrediction prediction) => ProviderScope(
      overrides: [
        f1EventPredictionProvider.overrideWith((ref, query) async => prediction),
      ],
      child: const MaterialApp(home: Scaffold(body: F1EventDetailPage(sportId: 'f1', eventId: '2026-5'))),
    );

void main() {
  testWidgets('no tab toggle at all when the event has no constructors block (e.g. a sprint)', (tester) async {
    await tester.pumpWidget(_wrap(_fieldPrediction()));
    await tester.pumpAndSettle();

    expect(find.text('Drivers'), findsNothing);
    expect(find.text('Constructors'), findsNothing);
    expect(find.text('Max Verstappen'), findsOneWidget);
  });

  testWidgets('defaults to the Drivers tab, Constructors table not built until tapped', (tester) async {
    final prediction = _fieldPrediction(constructors: [F1ConstructorPrediction(entityId: 'red_bull', name: 'Red Bull')]);
    await tester.pumpWidget(_wrap(prediction));
    await tester.pumpAndSettle();

    expect(find.text('Max Verstappen'), findsOneWidget);
    expect(find.text('Red Bull'), findsNothing);
  });

  testWidgets('tapping Constructors swaps to the constructors table', (tester) async {
    final prediction = _fieldPrediction(constructors: [F1ConstructorPrediction(entityId: 'red_bull', name: 'Red Bull')]);
    await tester.pumpWidget(_wrap(prediction));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Constructors'));
    await tester.pumpAndSettle();

    expect(find.text('Red Bull'), findsOneWidget);
    expect(find.text('Max Verstappen'), findsNothing);
  });

  testWidgets('constructors table falls back to a humanized id when name is null', (tester) async {
    final prediction = _fieldPrediction(constructors: [F1ConstructorPrediction(entityId: 'red_bull')]);
    await tester.pumpWidget(_wrap(prediction));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Constructors'));
    await tester.pumpAndSettle();

    expect(find.text('Red Bull'), findsOneWidget);
  });

  testWidgets(
      'shows a FINAL pill once ESPN reports the session over, even though our own storage hasn\'t caught up '
      'with the real result yet', (tester) async {
    // Regression: previously the page gave no visual signal at all that
    // the session had happened -- the LIVE pill only ever appeared while
    // state == "in", so it (correctly) never showed, but nothing took
    // its place once the race actually finished.
    await tester.pumpWidget(ProviderScope(
      overrides: [
        f1EventPredictionProvider.overrideWith((ref, query) async => _fieldPrediction()),
        f1LiveScoresProvider.overrideWith(
          (ref, sport) async => const {'2026-5': F1LiveEventState(eventType: 'field', state: 'post')},
        ),
      ],
      child: const MaterialApp(home: Scaffold(body: F1EventDetailPage(sportId: 'f1', eventId: '2026-5'))),
    ));
    await tester.pumpAndSettle();

    expect(find.text('FINAL'), findsOneWidget);
    expect(find.text('LIVE'), findsNothing);
  });

  testWidgets('shows the computing retry while the prediction is still computing', (tester) async {
    await tester.pumpWidget(ProviderScope(
      retry: (retryCount, error) => null,
      overrides: [
        f1EventPredictionProvider.overrideWith((ref, query) async => throw const PredictionComputingException(600)),
        f1LiveScoresProvider.overrideWith((ref, sport) async => const <String, F1LiveEventState>{}),
      ],
      child: const MaterialApp(home: Scaffold(body: F1EventDetailPage(sportId: 'f1', eventId: '2026-5'))),
    ));
    await tester.pump();
    await tester.pump();

    expect(find.text('Computing prediction...'), findsOneWidget);
    await tester.pump(const Duration(minutes: 20));
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('a stale prediction shows the freshness badge', (tester) async {
    const stale = F1EventPrediction(eventId: '2026-5', eventType: 'field', field: [], constructors: [], stale: true, staleRetryAfterSeconds: 600);
    await tester.pumpWidget(_wrap(stale));
    await tester.pump();
    await tester.pump();

    expect(find.byType(PredictionFreshnessBadge), findsOneWidget);
    await tester.pump(const Duration(minutes: 20));
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('polls the prediction and live scores every 30s', (tester) async {
    var predictionCalls = 0;
    var liveCalls = 0;
    await tester.pumpWidget(ProviderScope(
      overrides: [
        f1EventPredictionProvider.overrideWith((ref, query) async {
          predictionCalls++;
          return _fieldPrediction();
        }),
        f1LiveScoresProvider.overrideWith((ref, sport) async {
          liveCalls++;
          return const <String, F1LiveEventState>{};
        }),
      ],
      child: const MaterialApp(home: Scaffold(body: F1EventDetailPage(sportId: 'f1', eventId: '2026-5'))),
    ));
    await tester.pumpAndSettle();
    final (predictions, lives) = (predictionCalls, liveCalls);

    await tester.pump(const Duration(seconds: 31));
    await tester.pumpAndSettle();

    expect(predictionCalls, greaterThan(predictions));
    expect(liveCalls, greaterThan(lives));
    await tester.pumpWidget(const SizedBox());
  });
}
