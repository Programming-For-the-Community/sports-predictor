/// Mirrors GET /f1/predictions/events/{event_id}'s response shape -- see
/// Source/aws-lambdas/f1/predict/event_prediction.py's predict_field_event/
/// predict_sprint_event. Unlike PGA (field_prediction.dart's sealed
/// PgaEventPrediction union), F1 has no two-sided event shape at all -- a
/// "field" (main race) and a "sprint" (Sprint race) response share the
/// SAME driver-list shape, just with a different predictions key set (see
/// F1DriverPrediction's own field-by-field comments) and "field" alone
/// also carries a `constructors` block. One unified model, not a union --
/// the UI branches on eventType, not on runtime type.
library;

/// F1's own event_type values -- both F1Event's (f1_event.dart) and
/// F1EventPrediction's own isSprint getters, plus their fromJson
/// defaults and f1_live_score.dart's, reference these instead of
/// retyping the raw string. Field-shape-only concept -- head-to-head
/// sports have no event_type field. Separate from PGA's own PgaEventType
/// (field_prediction.dart) -- 'field' is coincidentally the same literal
/// in both, but F1 has no match_play/cup analog and PGA has no sprint
/// analog, so they're kept as two unrelated vocabularies.
abstract final class F1EventType {
  static const field = 'field';
  static const sprint = 'sprint';
}

class F1ModelValue {
  const F1ModelValue({required this.value, required this.modelVersion});

  final double value;
  final int modelVersion;

  factory F1ModelValue.fromJson(Map<String, dynamic> json) => F1ModelValue(
        value: (json['value'] as num).toDouble(),
        modelVersion: json['model_version'] as int,
      );
}

/// Real, already-stored result -- meaningful once qualifying has landed
/// even before the race itself runs, not just once "completed" (see
/// event_prediction.py's own _actual_driver_result docstring).
class F1ActualResult {
  const F1ActualResult({
    this.finishPosition, this.gridPosition, this.status, this.points, this.fastestLap, this.lapsCompleted,
    this.qualifyingPosition, this.qualifyingGapToPoleSeconds,
  });

  final int? finishPosition;
  final int? gridPosition;
  final String? status;
  final double? points;
  final bool? fastestLap;
  final int? lapsCompleted;
  final int? qualifyingPosition;
  final double? qualifyingGapToPoleSeconds;

  factory F1ActualResult.fromJson(Map<String, dynamic> json) {
    final qualifying = json['qualifying'] as Map<String, dynamic>?;
    return F1ActualResult(
      finishPosition: json['finish_position'] as int?,
      gridPosition: json['grid_position'] as int?,
      status: json['status'] as String?,
      points: (json['points'] as num?)?.toDouble(),
      fastestLap: json['fastest_lap'] as bool?,
      lapsCompleted: json['laps_completed'] as int?,
      qualifyingPosition: qualifying?['position'] as int?,
      qualifyingGapToPoleSeconds: (qualifying?['gap_to_pole_seconds'] as num?)?.toDouble(),
    );
  }
}

class F1DriverPrediction {
  const F1DriverPrediction({
    required this.entityId,
    this.name,
    this.constructorEntityId,
    this.constructorName,
    this.winProbability,
    this.podiumProbability,
    this.projectedFinishPosition,
    this.dnfProbability,
    this.projectedQualifyingPosition,
    this.projectedGridPosition,
    this.actual,
  });

  final String entityId;
  final String? name;
  final String? constructorEntityId;
  // The constructor's own real display name (e.g. "Red Bull") --
  // event_prediction.py's own _driver_entry_base, NOT derived from
  // constructorEntityId (a lowercase/underscored id like "red_bull") on
  // the frontend. Null only when the constructor entity itself couldn't
  // be resolved -- f1_leaderboard_table.dart falls back to humanizing
  // constructorEntityId in that case, never showing the raw id verbatim.
  final String? constructorName;
  final F1ModelValue? winProbability;
  final F1ModelValue? podiumProbability;
  // "field" event only -- null for a sprint entry.
  final F1ModelValue? projectedFinishPosition;
  final F1ModelValue? dnfProbability;
  final F1ModelValue? projectedQualifyingPosition;
  // "sprint" event only -- null for a field entry.
  final F1ModelValue? projectedGridPosition;
  final F1ActualResult? actual;

  factory F1DriverPrediction.fromJson(Map<String, dynamic> json) {
    final predictions = json['predictions'] as Map<String, dynamic>? ?? {};
    F1ModelValue? modelValue(String key) =>
        predictions[key] != null ? F1ModelValue.fromJson(predictions[key] as Map<String, dynamic>) : null;
    return F1DriverPrediction(
      entityId: json['entity_id'] as String,
      name: json['name'] as String?,
      constructorEntityId: json['constructor_entity_id'] as String?,
      constructorName: json['constructor_name'] as String?,
      winProbability: modelValue('win_probability'),
      podiumProbability: modelValue('podium_probability'),
      projectedFinishPosition: modelValue('projected_finish_position'),
      dnfProbability: modelValue('dnf_probability'),
      projectedQualifyingPosition: modelValue('projected_qualifying_position'),
      projectedGridPosition: modelValue('projected_grid_position'),
      actual: json['actual'] != null ? F1ActualResult.fromJson(json['actual'] as Map<String, dynamic>) : null,
    );
  }
}

class F1ConstructorPrediction {
  const F1ConstructorPrediction({required this.entityId, this.name, this.winProbability});

  final String entityId;
  final String? name;
  final F1ModelValue? winProbability;

  factory F1ConstructorPrediction.fromJson(Map<String, dynamic> json) {
    final predictions = json['predictions'] as Map<String, dynamic>? ?? {};
    return F1ConstructorPrediction(
      entityId: json['entity_id'] as String,
      name: json['name'] as String?,
      winProbability: predictions['win_probability'] != null
          ? F1ModelValue.fromJson(predictions['win_probability'] as Map<String, dynamic>)
          : null,
    );
  }
}

class F1EventPrediction {
  const F1EventPrediction({
    required this.eventId,
    required this.eventType,
    required this.field,
    this.raceName,
    this.status,
    this.circuitId,
    this.season,
    this.week,
    this.constructors = const [],
    this.stale = false,
    this.staleRetryAfterSeconds,
  });

  final String eventId;
  final String eventType; // 'field' | 'sprint'
  final String? raceName;
  final String? status;
  final String? circuitId;
  final int? season;
  final int? week;
  // Server-side sorted ascending by projected_finish_position/grid
  // position (event_prediction.py's own _field_sort_key) -- a reasonable
  // order before any real result exists, and the order shown whenever no
  // live overlay is present. f1_leaderboard_table.dart re-sorts by live
  // running order instead once f1LiveScoresProvider has one, same
  // "real signal first, else the model's own order" idea PGA's own
  // re-sort-by-live-standing uses.
  final List<F1DriverPrediction> field;
  // Empty for a "sprint" event -- no constructor model is scored per
  // Sprint race (see event_prediction.py's predict_sprint_event).
  final List<F1ConstructorPrediction> constructors;
  final bool stale;
  final int? staleRetryAfterSeconds;

  bool get isSprint => eventType == F1EventType.sprint;

  factory F1EventPrediction.fromJson(Map<String, dynamic> json) => F1EventPrediction(
        eventId: json['event_id'] as String,
        eventType: json['event_type'] as String? ?? F1EventType.field,
        raceName: json['race_name'] as String?,
        status: json['status'] as String?,
        circuitId: json['circuit_id'] as String?,
        season: json['season'] as int?,
        week: json['week'] as int?,
        field: (json['field'] as List<dynamic>? ?? [])
            .map((p) => F1DriverPrediction.fromJson(p as Map<String, dynamic>))
            .toList(),
        constructors: (json['constructors'] as List<dynamic>? ?? [])
            .map((c) => F1ConstructorPrediction.fromJson(c as Map<String, dynamic>))
            .toList(),
        stale: json['stale'] as bool? ?? false,
        staleRetryAfterSeconds: json['retry_after_seconds'] as int?,
      );
}
