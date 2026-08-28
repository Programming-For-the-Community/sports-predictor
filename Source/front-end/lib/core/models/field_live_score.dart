/// Mirrors GET /pga/live-scores' response shape for PGA -- see
/// Source/aws-lambdas/pga/live-scores/live_scores.py. Deliberately lean
/// (no golfer names -- the frontend already has names from GET
/// /pga/events/predictions and merges by entity_id) and framed as a
/// fresher leaderboard SNAPSHOT, not literal real-time scores (PGA has
/// no hole-level granularity anywhere in this data -- see
/// project-pga-onboarding memory).
library;

class FieldParticipantLiveResult {
  const FieldParticipantLiveResult({this.finishPosition, this.isTie = false, this.status, this.scoreToPar, this.totalStrokes});

  // Same fields as FieldParticipantResult (field_event.dart) --
  // intentionally a SEPARATE class, not reused, since this comes from a
  // different endpoint/cache with its own independent evolution path
  // (same precedent as LiveEventState never being reused as
  // ParticipantResult in the head-to-head models).
  final int? finishPosition;
  final bool isTie;
  final String? status;
  final num? scoreToPar;
  final double? totalStrokes;

  factory FieldParticipantLiveResult.fromJson(Map<String, dynamic> json) => FieldParticipantLiveResult(
        finishPosition: json['finish_position'] as int?,
        isTie: json['is_tie'] as bool? ?? false,
        status: json['status'] as String?,
        scoreToPar: json['score_to_par'] as num?,
        totalStrokes: (json['total_strokes'] as num?)?.toDouble(),
      );
}

class FieldLiveEventState {
  const FieldLiveEventState({required this.status, this.tournamentName, this.participants = const {}});

  // Tournament-level 'scheduled' | 'completed' (map_status vocabulary).
  final String status;
  final String? tournamentName;
  final Map<String, FieldParticipantLiveResult> participants; // entity_id -> live result

  factory FieldLiveEventState.fromJson(Map<String, dynamic> json) {
    final participantsJson = json['participants'] as Map<String, dynamic>? ?? {};
    return FieldLiveEventState(
      status: json['status'] as String? ?? '',
      tournamentName: json['tournament_name'] as String?,
      participants: participantsJson.map(
        (entityId, value) => MapEntry(entityId, FieldParticipantLiveResult.fromJson(value as Map<String, dynamic>)),
      ),
    );
  }
}
