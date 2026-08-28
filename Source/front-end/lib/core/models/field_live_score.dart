/// Mirrors GET /pga/live-scores' response shape for PGA -- see
/// Source/aws-lambdas/pga/live-scores/live_scores.py. Deliberately lean
/// (no golfer/team names -- the frontend already has names from GET
/// /pga/events/predictions and merges by entity_id) and framed as a
/// fresher leaderboard SNAPSHOT, not literal real-time scores (PGA has
/// no hole-level granularity anywhere in this data -- see
/// project-pga-onboarding memory).
///
/// Each cache entry carries an explicit `event_type` -- "field" entries
/// parse into FieldLiveEventState (finish_position/score_to_par per
/// golfer), "match_play"/"cup" entries parse into TwoSidedLiveEventState
/// (won/halved/margin or points per side). See parsePgaLiveEventState for
/// the dispatch, same pattern field_prediction.dart's own
/// parsePgaEventPrediction uses.
library;

class FieldParticipantLiveResult {
  const FieldParticipantLiveResult({this.finishPosition, this.isTie = false, this.status, this.scoreToPar, this.totalStrokes});

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

/// One side's (or golfer's) live match_play/cup result -- covers both
/// shapes in one lean class, same "independently nullable fields" pattern
/// FieldParticipantLiveResult uses: a match result carries
/// status/won/halved/marginDisplay/marginHoles (library/normalize/
/// pga_matchplay.py's own _match_result), a cup result carries
/// points/won/halved (leaderboard_event_to_cup_event_item) -- never both
/// at once, but keeping one class avoids a second near-duplicate type for
/// what's structurally the same "who's ahead" concept.
class TwoSidedParticipantLiveResult {
  const TwoSidedParticipantLiveResult({this.status, this.won, this.halved, this.marginDisplay, this.marginHoles, this.points});

  final String? status;
  final bool? won;
  final bool? halved;
  final String? marginDisplay;
  final double? marginHoles;
  final double? points;

  factory TwoSidedParticipantLiveResult.fromJson(Map<String, dynamic> json) => TwoSidedParticipantLiveResult(
        status: json['status'] as String?,
        won: json['won'] as bool?,
        halved: json['halved'] as bool?,
        marginDisplay: json['margin_display'] as String?,
        marginHoles: (json['margin_holes'] as num?)?.toDouble(),
        points: (json['points'] as num?)?.toDouble(),
      );
}

class TwoSidedLiveEventState {
  const TwoSidedLiveEventState({required this.eventType, required this.status, this.tournamentName, this.participants = const {}});

  final String eventType; // 'match_play' | 'cup'
  final String status;
  final String? tournamentName;
  final Map<String, TwoSidedParticipantLiveResult> participants; // entity_id -> live result

  factory TwoSidedLiveEventState.fromJson(Map<String, dynamic> json) {
    final participantsJson = json['participants'] as Map<String, dynamic>? ?? {};
    return TwoSidedLiveEventState(
      eventType: json['event_type'] as String,
      status: json['status'] as String? ?? '',
      tournamentName: json['tournament_name'] as String?,
      participants: participantsJson.map(
        (entityId, value) => MapEntry(entityId, TwoSidedParticipantLiveResult.fromJson(value as Map<String, dynamic>)),
      ),
    );
  }
}

/// Discriminated union of the two possible per-entry live-score shapes.
sealed class PgaLiveEventState {}

class PgaFieldLiveState extends PgaLiveEventState {
  PgaFieldLiveState(this.state);
  final FieldLiveEventState state;
}

class PgaTwoSidedLiveState extends PgaLiveEventState {
  PgaTwoSidedLiveState(this.state);
  final TwoSidedLiveEventState state;
}

/// Dispatches one cache entry on its own `event_type` key -- 'field'
/// entries predate this key's addition on the backend and default to
/// 'field' when absent, so an old cached entry (before this pass's
/// backend deploy lands) still parses instead of throwing.
PgaLiveEventState parsePgaLiveEventState(Map<String, dynamic> json) {
  final eventType = json['event_type'] as String? ?? 'field';
  switch (eventType) {
    case 'field':
      return PgaFieldLiveState(FieldLiveEventState.fromJson(json));
    case 'match_play':
    case 'cup':
      return PgaTwoSidedLiveState(TwoSidedLiveEventState.fromJson(json));
    default:
      throw FormatException('Unrecognized PGA live-scores event_type: $eventType');
  }
}
