import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/f1_events_repository.dart';
import 'package:front_end/core/models/f1_prediction.dart';
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
}
