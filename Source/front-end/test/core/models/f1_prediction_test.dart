import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/f1_prediction.dart';

void main() {
  test('parses a full field-event prediction with drivers and constructors', () {
    final prediction = F1EventPrediction.fromJson({
      'sport': 'f1', 'event_id': '2026-5', 'event_type': 'field', 'race_name': 'Monaco Grand Prix',
      'status': 'scheduled', 'circuit_id': 'monaco', 'season': 2026, 'week': 5,
      'field': [
        {
          'entity_id': 'max_verstappen', 'name': 'Max Verstappen', 'constructor_entity_id': 'red_bull',
          'predictions': {
            'win_probability': {'value': 0.42, 'model_version': 3},
            'podium_probability': {'value': 0.71, 'model_version': 3},
            'projected_finish_position': {'value': 1.8, 'model_version': 2},
            'dnf_probability': {'value': 0.05, 'model_version': 1},
            'projected_qualifying_position': {'value': 1.2, 'model_version': 1},
          },
          'actual': {
            'finish_position': 1, 'grid_position': 1, 'status': 'finished', 'points': 25.0, 'fastest_lap': true,
            'laps_completed': 78, 'qualifying': {'position': 1, 'gap_to_pole_seconds': 0.0},
          },
        },
      ],
      'constructors': [
        {'entity_id': 'red_bull', 'name': 'Red Bull', 'predictions': {'win_probability': {'value': 0.55, 'model_version': 2}}},
      ],
      'generated_at': '2026-05-24T00:00:00Z',
    });

    expect(prediction.eventType, 'field');
    expect(prediction.isSprint, isFalse);
    final driver = prediction.field.single;
    expect(driver.winProbability!.value, 0.42);
    expect(driver.projectedFinishPosition!.value, 1.8);
    expect(driver.projectedGridPosition, isNull); // field-only response, no sprint key
    expect(driver.actual!.finishPosition, 1);
    expect(driver.actual!.qualifyingPosition, 1);
    expect(driver.actual!.qualifyingGapToPoleSeconds, 0.0);

    final constructor = prediction.constructors.single;
    expect(constructor.entityId, 'red_bull');
    expect(constructor.winProbability!.value, 0.55);
  });

  test('parses a sprint-event prediction with no constructors block', () {
    final prediction = F1EventPrediction.fromJson({
      'event_id': '2026-5-sprint', 'event_type': 'sprint', 'status': 'scheduled',
      'field': [
        {
          'entity_id': 'max_verstappen', 'name': 'Max Verstappen',
          'predictions': {
            'win_probability': {'value': 0.3, 'model_version': 1},
            'podium_probability': {'value': 0.6, 'model_version': 1},
            'projected_grid_position': {'value': 2.1, 'model_version': 1},
          },
        },
      ],
    });

    expect(prediction.isSprint, isTrue);
    expect(prediction.constructors, isEmpty);
    final driver = prediction.field.single;
    expect(driver.projectedGridPosition!.value, 2.1);
    expect(driver.projectedFinishPosition, isNull); // sprint-only response, no field key
    expect(driver.dnfProbability, isNull);
  });

  test('a driver with no promoted models yet has every prediction field null, not a crash', () {
    final prediction = F1EventPrediction.fromJson({
      'event_id': '2026-9', 'event_type': 'field', 'status': 'scheduled',
      'field': [
        {'entity_id': 'rookie_driver', 'predictions': <String, dynamic>{}},
      ],
    });

    final driver = prediction.field.single;
    expect(driver.winProbability, isNull);
    expect(driver.actual, isNull);
  });

  test('stale + retry_after_seconds parse from the predict-read cache wrapper', () {
    final prediction = F1EventPrediction.fromJson({
      'event_id': '2026-9', 'event_type': 'field', 'status': 'scheduled', 'field': [],
      'stale': true, 'retry_after_seconds': 5,
    });

    expect(prediction.stale, isTrue);
    expect(prediction.staleRetryAfterSeconds, 5);
  });
}
