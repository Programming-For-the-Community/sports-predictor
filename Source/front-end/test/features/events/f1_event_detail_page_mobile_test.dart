import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/f1_events_repository.dart';
import 'package:front_end/core/data/live_scores_repository.dart';
import 'package:front_end/core/models/f1_live_score.dart';
import 'package:front_end/core/models/f1_prediction.dart';
import 'package:front_end/features/events/f1_event_detail_page.dart';

import '../../support/mobile_viewport.dart';

final _fieldPrediction = F1EventPrediction(
  eventId: '2026-14',
  eventType: 'field',
  raceName: 'Formula 1 Gran Premio Heineken D\'Italia',
  field: [
    F1DriverPrediction(
      entityId: 'max_verstappen', name: 'Max Emilian Verstappen',
      constructorEntityId: 'red_bull_racing', constructorName: 'Oracle Red Bull Racing',
      projectedFinishPosition: const F1ModelValue(value: 1.0, modelVersion: 3),
      winProbability: const F1ModelValue(value: 0.34, modelVersion: 3),
      podiumProbability: const F1ModelValue(value: 0.71, modelVersion: 3),
      dnfProbability: const F1ModelValue(value: 0.08, modelVersion: 3),
    ),
    F1DriverPrediction(
      entityId: 'lando_norris', name: 'Lando Norris',
      constructorEntityId: 'mclaren', constructorName: 'McLaren Formula 1 Team',
      projectedFinishPosition: const F1ModelValue(value: 2.0, modelVersion: 3),
      winProbability: const F1ModelValue(value: 0.28, modelVersion: 3),
      podiumProbability: const F1ModelValue(value: 0.64, modelVersion: 3),
      dnfProbability: const F1ModelValue(value: 0.06, modelVersion: 3),
    ),
  ],
  constructors: const [
    F1ConstructorPrediction(entityId: 'red_bull_racing', name: 'Oracle Red Bull Racing', winProbability: F1ModelValue(value: 0.41, modelVersion: 3)),
    F1ConstructorPrediction(entityId: 'mclaren', name: 'McLaren Formula 1 Team', winProbability: F1ModelValue(value: 0.35, modelVersion: 3)),
  ],
);

final _sprintPrediction = F1EventPrediction(
  eventId: '2026-14-sprint',
  eventType: 'sprint',
  raceName: 'Sprint',
  field: [
    F1DriverPrediction(
      entityId: 'max_verstappen', name: 'Max Emilian Verstappen',
      constructorEntityId: 'red_bull_racing', constructorName: 'Oracle Red Bull Racing',
      projectedGridPosition: const F1ModelValue(value: 1.0, modelVersion: 3),
      winProbability: const F1ModelValue(value: 0.3, modelVersion: 3),
      podiumProbability: const F1ModelValue(value: 0.6, modelVersion: 3),
    ),
  ],
);

/// Dedicated mobile check for F1EventDetailPage's own header (race name +
/// LiveStatusPill + SprintBadge sharing one Row) and Drivers/Constructors
/// tab toggle -- F1LeaderboardTable's own compact-column behavior is
/// covered directly in f1_leaderboard_table_test.dart, this file's job is
/// the surrounding page chrome those columns sit inside of.
void main() {
  for (final width in mobileViewportWidths) {
    testWidgets('a field race renders with no overflow at ${width}px wide', (tester) async {
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [
            f1EventPredictionProvider.overrideWith((ref, query) async => _fieldPrediction),
            f1LiveScoresProvider.overrideWith((ref, sport) async => const {}),
          ],
          child: const MaterialApp(home: Scaffold(body: F1EventDetailPage(sportId: 'f1', eventId: '2026-14'))),
        ),
      );

      expect(tester.takeException(), isNull);
    });

    testWidgets('a live field race renders with no overflow at ${width}px wide', (tester) async {
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [
            f1EventPredictionProvider.overrideWith((ref, query) async => _fieldPrediction),
            f1LiveScoresProvider.overrideWith(
              (ref, sport) async => {
                '2026-14': const F1LiveEventState(
                  eventType: 'field', status: 'In Progress', state: 'in', raceName: 'Formula 1 Gran Premio Heineken D\'Italia',
                  participants: {'max_verstappen': F1DriverLiveResult(order: 1, winner: false)},
                ),
              },
            ),
          ],
          child: const MaterialApp(home: Scaffold(body: F1EventDetailPage(sportId: 'f1', eventId: '2026-14'))),
        ),
      );

      expect(tester.takeException(), isNull);
    });

    testWidgets('the Constructors tab renders with no overflow at ${width}px wide', (tester) async {
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [
            f1EventPredictionProvider.overrideWith((ref, query) async => _fieldPrediction),
            f1LiveScoresProvider.overrideWith((ref, sport) async => const {}),
          ],
          child: const MaterialApp(home: Scaffold(body: F1EventDetailPage(sportId: 'f1', eventId: '2026-14'))),
        ),
      );
      await tester.tap(find.text('Constructors'));
      await tester.pumpAndSettle();

      expect(tester.takeException(), isNull);
    });

    // Densest header case: title + LIVE pill + SPRINT badge all sharing
    // one Row at once, no Constructors tab to compete for space with.
    testWidgets('a live sprint renders with no overflow at ${width}px wide', (tester) async {
      await pumpAtWidth(
        tester,
        width,
        ProviderScope(
          overrides: [
            f1EventPredictionProvider.overrideWith((ref, query) async => _sprintPrediction),
            f1LiveScoresProvider.overrideWith(
              (ref, sport) async => {
                '2026-14-sprint': const F1LiveEventState(
                  eventType: 'sprint', status: 'In Progress', state: 'in',
                  participants: {'max_verstappen': F1DriverLiveResult(order: 1, winner: false)},
                ),
              },
            ),
          ],
          child: const MaterialApp(home: Scaffold(body: F1EventDetailPage(sportId: 'f1', eventId: '2026-14-sprint'))),
        ),
      );

      expect(tester.takeException(), isNull);
    });
  }
}
