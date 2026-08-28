/// Mirrors GET /pga/predictions/events/{event_id}'s response shape --
/// see Source/aws-lambdas/pga/predict/event_prediction.py. Unlike every
/// head-to-head sport, this route can return one of TWO genuinely
/// different shapes depending on the event's own event_type:
/// "field" -> FieldEventPrediction (a ranked list of every golfer, never
/// collapsed to a scalar -- design/FRONTEND_STYLE.md); "match_play"/"cup"
/// -> TwoSidedPgaPrediction (home/away, same framing as a head-to-head
/// sport since those genuinely are 2-sided). See parsePgaEventPrediction
/// below for the dispatch.
library;

/// One promoted model's scored value -- independently nullable per model
/// throughout this file (a response is never all-or-nothing just because
/// one model hasn't been promoted yet).
class ModelValue {
  const ModelValue({required this.value, required this.modelVersion});

  final double value;
  final int modelVersion;

  factory ModelValue.fromJson(Map<String, dynamic> json) => ModelValue(
        value: (json['value'] as num).toDouble(),
        modelVersion: json['model_version'] as int,
      );
}

class Cutline {
  const Cutline({this.projectedCutScore});

  // Null when no cutline model has been promoted yet -- independent of
  // whether this specific tournament turns out to have a cut at all (no
  // reliable pre-tournament signal exists to omit cutline selectively,
  // see project-pga-onboarding memory).
  final ModelValue? projectedCutScore;

  factory Cutline.fromJson(Map<String, dynamic> json) => Cutline(
        projectedCutScore: json['projected_cut_score'] != null
            ? ModelValue.fromJson(json['projected_cut_score'] as Map<String, dynamic>)
            : null,
      );
}

/// One already-played round's real result -- Source/library/normalize/
/// pga.py's own _parse_rounds shape, surfaced (not status-gated) via
/// event_prediction.py's _actual_golfer_result.
class ActualRoundResult {
  const ActualRoundResult({required this.round, this.scoreToPar, this.totalStrokes});

  final int round;
  final num? scoreToPar;
  final double? totalStrokes;

  factory ActualRoundResult.fromJson(Map<String, dynamic> json) => ActualRoundResult(
        round: json['round'] as int,
        scoreToPar: json['score_to_par'] as num?,
        totalStrokes: (json['total_strokes'] as num?)?.toDouble(),
      );
}

class FieldParticipantPrediction {
  const FieldParticipantPrediction({
    required this.entityId,
    this.name,
    this.country,
    this.top10Probability,
    this.top5Probability,
    this.projectedScoreToPar,
    this.rounds = const {},
    this.actualFinishPosition,
    this.actualScoreToPar,
    this.actualRounds = const {},
    this.actualStatus,
  });

  final String entityId;
  final String? name;
  final String? country;
  final ModelValue? top10Probability;
  final ModelValue? top5Probability;
  final ModelValue? projectedScoreToPar;
  // Parsed from predictions['rounds']['round_2'] etc -- 0 or 1 entries in
  // practice (applicable_rounds always returns at most one round, see
  // aws-lambdas/pga/predict/live_features.py).
  final Map<int, ModelValue> rounds;
  // Not gated on the whole EVENT's status being 'completed' -- a real
  // current standing/completed round is meaningful throughout the
  // tournament (event_prediction.py's _actual_golfer_result docstring).
  // event_prediction.py returns null for a golfer with no real result
  // data at all yet (`actual` itself absent), treated identically here.
  final int? actualFinishPosition;
  final double? actualScoreToPar;
  // Real per-round results already played, keyed by round number --
  // pairs with `rounds` (the PROJECTED per-round model output) for a
  // proj-vs-actual comparison per round.
  final Map<int, ActualRoundResult> actualRounds;
  // This golfer's own real ESPN status (scheduled/finished/cut/
  // made_cut_did_not_finish/withdrawn) -- NOT inferred from
  // actualFinishPosition's presence (a real current standing exists well
  // before this golfer's own round is actually finished). Used by
  // FieldLeaderboardTable's STATUS column outside the live-poll window.
  final String? actualStatus;

  factory FieldParticipantPrediction.fromJson(Map<String, dynamic> json) {
    final predictions = json['predictions'] as Map<String, dynamic>? ?? {};
    final roundsJson = predictions['rounds'] as Map<String, dynamic>? ?? {};
    final rounds = <int, ModelValue>{};
    for (final entry in roundsJson.entries) {
      final roundNumber = int.tryParse(entry.key.replaceFirst('round_', ''));
      if (roundNumber != null) {
        rounds[roundNumber] = ModelValue.fromJson(entry.value as Map<String, dynamic>);
      }
    }
    final actual = json['actual'] as Map<String, dynamic>?;
    final actualRoundsJson = actual?['rounds'] as List<dynamic>? ?? [];
    final actualRounds = <int, ActualRoundResult>{
      for (final entry in actualRoundsJson) ActualRoundResult.fromJson(entry as Map<String, dynamic>).round: ActualRoundResult.fromJson(entry),
    };
    return FieldParticipantPrediction(
      entityId: json['entity_id'] as String,
      name: json['name'] as String?,
      country: json['country'] as String?,
      top10Probability: predictions['top_10_probability'] != null
          ? ModelValue.fromJson(predictions['top_10_probability'] as Map<String, dynamic>)
          : null,
      top5Probability: predictions['top_5_probability'] != null
          ? ModelValue.fromJson(predictions['top_5_probability'] as Map<String, dynamic>)
          : null,
      projectedScoreToPar: predictions['projected_score_to_par'] != null
          ? ModelValue.fromJson(predictions['projected_score_to_par'] as Map<String, dynamic>)
          : null,
      rounds: rounds,
      actualFinishPosition: actual?['finish_position'] as int?,
      actualScoreToPar: (actual?['score_to_par'] as num?)?.toDouble(),
      actualRounds: actualRounds,
      actualStatus: actual?['status'] as String?,
    );
  }
}

class FieldEventPrediction {
  const FieldEventPrediction({
    required this.eventId,
    required this.field,
    this.tournamentName,
    this.status,
    this.cutline,
    this.stale = false,
    this.staleRetryAfterSeconds,
  });

  final String eventId;
  final String? tournamentName;
  final String? status;
  final Cutline? cutline;
  // PRE-SORTED server-side (event_prediction.py's _field_sort_key), by
  // PROJECTION -- a reasonable order before any real standing exists.
  // Once real standings exist (live or actual), FieldLeaderboardTable
  // itself re-sorts by real current standing first, falling back to this
  // projected order only for golfers with no real standing yet -- server-
  // side sorting alone can't stay correct once live data moves every 5
  // minutes but this cached response doesn't (see field_leaderboard_table.
  // dart's own _sortedByStanding).
  final List<FieldParticipantPrediction> field;
  final bool stale;
  final int? staleRetryAfterSeconds;

  factory FieldEventPrediction.fromJson(Map<String, dynamic> json) => FieldEventPrediction(
        eventId: json['event_id'] as String,
        tournamentName: json['tournament_name'] as String?,
        status: json['status'] as String?,
        cutline: json['cutline'] != null ? Cutline.fromJson(json['cutline'] as Map<String, dynamic>) : null,
        field: (json['field'] as List<dynamic>? ?? [])
            .map((p) => FieldParticipantPrediction.fromJson(p as Map<String, dynamic>))
            .toList(),
        stale: json['stale'] as bool? ?? false,
        staleRetryAfterSeconds: json['retry_after_seconds'] as int?,
      );
}

class MatchPlaySideGolfer {
  const MatchPlaySideGolfer({required this.entityId, this.name, this.country});

  final String entityId;
  final String? name;
  final String? country;

  factory MatchPlaySideGolfer.fromJson(Map<String, dynamic> json) => MatchPlaySideGolfer(
        entityId: json['entity_id'] as String,
        name: json['name'] as String?,
        country: json['country'] as String?,
      );
}

class MatchPlaySide {
  const MatchPlaySide({required this.entityId, this.name, this.golfers});

  final String entityId;
  final String? name;
  // Present only for a TEAM side (Ryder/Presidents Cup foursomes/singles)
  // -- never for a Cup's own home/away (always team-typed, no golfers
  // key) or an individual WGC match_play side.
  final List<MatchPlaySideGolfer>? golfers;

  factory MatchPlaySide.fromJson(Map<String, dynamic> json) => MatchPlaySide(
        entityId: json['entity_id'] as String,
        name: json['name'] as String?,
        golfers: json['golfers'] != null
            ? (json['golfers'] as List<dynamic>).map((g) => MatchPlaySideGolfer.fromJson(g as Map<String, dynamic>)).toList()
            : null,
      );
}

/// Backs BOTH event_type 'match_play' and 'cup' -- event_prediction.py's
/// own _predict_two_sided_event produces one identical shape for both,
/// differing only in which predictions key is populated (match_
/// win_probability vs. cup_win_probability) and what matchFormat/
/// sessionName mean (always null for a cup).
class TwoSidedPgaPrediction {
  const TwoSidedPgaPrediction({
    required this.eventId,
    required this.eventType,
    this.tournamentName,
    this.status,
    this.matchFormat,
    this.sessionName,
    this.home,
    this.away,
    this.winProbability,
    this.actualHomeWon,
    this.actualHalved,
    this.stale = false,
    this.staleRetryAfterSeconds,
  });

  final String eventId;
  final String eventType; // 'match_play' | 'cup'
  final String? tournamentName;
  final String? status;
  final String? matchFormat;
  final String? sessionName;
  final MatchPlaySide? home;
  final MatchPlaySide? away;
  // predictions['match_win_probability'] or predictions['cup_win_probability']
  // -- predictions can be {} (no model promoted yet), so this is nullable.
  final ModelValue? winProbability;
  // Both only present when status == 'completed'.
  final bool? actualHomeWon;
  final bool? actualHalved;
  final bool stale;
  final int? staleRetryAfterSeconds;

  factory TwoSidedPgaPrediction.fromJson(Map<String, dynamic> json) {
    final predictions = json['predictions'] as Map<String, dynamic>? ?? {};
    final winProbabilityJson = (predictions['match_win_probability'] ?? predictions['cup_win_probability']) as Map<String, dynamic>?;
    final actual = json['actual'] as Map<String, dynamic>?;
    return TwoSidedPgaPrediction(
      eventId: json['event_id'] as String,
      eventType: json['event_type'] as String,
      tournamentName: json['tournament_name'] as String?,
      status: json['status'] as String?,
      matchFormat: json['match_format'] as String?,
      sessionName: json['session_name'] as String?,
      home: json['home'] != null ? MatchPlaySide.fromJson(json['home'] as Map<String, dynamic>) : null,
      away: json['away'] != null ? MatchPlaySide.fromJson(json['away'] as Map<String, dynamic>) : null,
      winProbability: winProbabilityJson != null ? ModelValue.fromJson(winProbabilityJson) : null,
      actualHomeWon: actual?['home_won'] as bool?,
      actualHalved: actual?['halved'] as bool?,
      stale: json['stale'] as bool? ?? false,
      staleRetryAfterSeconds: json['retry_after_seconds'] as int?,
    );
  }
}

/// Discriminated union of the two possible /pga/predictions/events/{id}
/// shapes -- see parsePgaEventPrediction for the dispatch. Callers
/// pattern-match on the concrete type (Dart 3 sealed class + switch, same
/// syntax season_page.dart already uses).
sealed class PgaEventPrediction {}

class PgaFieldPrediction extends PgaEventPrediction {
  PgaFieldPrediction(this.prediction);
  final FieldEventPrediction prediction;
}

class PgaTwoSidedPrediction extends PgaEventPrediction {
  PgaTwoSidedPrediction(this.prediction);
  final TwoSidedPgaPrediction prediction;
}

/// Dispatches on `event_type` -- call AFTER checking for the
/// PredictionComputingException 'computing' shape (see
/// field_events_repository.dart), same order events_repository.dart's
/// own getEventPrediction already establishes for the head-to-head shape.
PgaEventPrediction parsePgaEventPrediction(Map<String, dynamic> json) {
  final eventType = json['event_type'] as String?;
  switch (eventType) {
    case 'field':
      return PgaFieldPrediction(FieldEventPrediction.fromJson(json));
    case 'match_play':
    case 'cup':
      return PgaTwoSidedPrediction(TwoSidedPgaPrediction.fromJson(json));
    default:
      throw FormatException('Unrecognized PGA event_type: $eventType');
  }
}
