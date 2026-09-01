/// Mirrors GET /f1/live-scores' response shape -- see
/// Source/aws-lambdas/f1/live-scores/live_scores.py's get_live_scores. Own
/// dedicated model, not a reuse of field_live_score.dart's PGA-shaped
/// classes -- F1's live cache carries a genuinely different shape (ESPN's
/// own per-competitor `order`/`winner`, no per-driver status vocabulary
/// at all) since it's sourced from ESPN, not Jolpica (see live_scores.py's
/// own docstring for why). Deliberately lean, same reasoning
/// field_live_score.dart's own doc comment gives: no driver/constructor
/// names here -- the frontend already has those from GET
/// /f1/events/predictions and merges by entity_id.
library;

import 'f1_prediction.dart' show F1EventType;

class F1DriverLiveResult {
  const F1DriverLiveResult({this.order, this.winner = false});

  // 1-based running/finishing order in this session, as ESPN reports it
  // live -- null if ESPN hasn't assigned this competitor a position yet
  // (e.g. formation lap, or a driver ESPN's own feed hasn't placed).
  final int? order;
  final bool winner;

  factory F1DriverLiveResult.fromJson(Map<String, dynamic> json) => F1DriverLiveResult(
        order: json['order'] as int?,
        winner: json['winner'] as bool? ?? false,
      );
}

class F1LiveEventState {
  const F1LiveEventState({required this.eventType, this.status, this.state, this.raceName, this.participants = const {}});

  final String eventType; // 'field' | 'sprint'
  final String? status; // ESPN's own status.type.name (display text)
  final String? state; // ESPN's own status.type.state -- 'pre' | 'in' | 'post'
  final String? raceName;
  final Map<String, F1DriverLiveResult> participants; // entity_id -> live result

  // Session is actually running right now, per ESPN. A cache entry can
  // still be present with state == 'post' for a while after the checkered
  // flag (live_scores.py's own END_BUFFER tail) -- callers wanting "still
  // worth showing a live overlay for" should check presence in the map,
  // not just this flag; callers wanting "put a LIVE badge on it" should
  // check this flag specifically.
  bool get isLive => state == 'in';

  factory F1LiveEventState.fromJson(Map<String, dynamic> json) {
    final participantsJson = json['participants'] as Map<String, dynamic>? ?? {};
    return F1LiveEventState(
      eventType: json['event_type'] as String? ?? F1EventType.field,
      status: json['status'] as String?,
      state: json['state'] as String?,
      raceName: json['race_name'] as String?,
      participants: participantsJson.map(
        (entityId, value) => MapEntry(entityId, F1DriverLiveResult.fromJson(value as Map<String, dynamic>)),
      ),
    );
  }
}
