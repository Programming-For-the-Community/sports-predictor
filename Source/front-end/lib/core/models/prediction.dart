import 'event_leaders.dart';

/// Mirrors GET /{sport}/predictions/events/{event_id}'s response shape
/// (see Source/aws-lambdas/nfl/predict/handler.py's _predict_event).
/// `leaders` is optional -- best-effort server-side (see event_leaders.dart).
class EventPrediction {
  const EventPrediction({
    required this.homeWinProbability,
    required this.homeWinProbabilityModelVersion,
    required this.margin,
    required this.homeScore,
    required this.awayScore,
    required this.leaders,
    this.stale = false,
    this.staleRetryAfterSeconds,
  });

  final double homeWinProbability;
  final int homeWinProbabilityModelVersion;
  final double margin;
  final double homeScore;
  final double awayScore;
  final EventLeaders? leaders;
  // True on a 203 response -- predict-read served this from a cache entry
  // it already knows is out of date (model repromotion, or the freshness
  // TTL) and kicked off a background refresh at the same time. Defaults
  // false when the field is absent. See core/widgets/prediction_freshness_badge.dart.
  final bool stale;
  // Only meaningful when stale -- the server's own cadence hint (same
  // field/purpose as PredictionComputingException.retryAfterSeconds).
  final int? staleRetryAfterSeconds;

  factory EventPrediction.fromJson(Map<String, dynamic> json) {
    final predictions = json['predictions'] as Map<String, dynamic>;
    final winProbability = predictions['win_probability'] as Map<String, dynamic>;
    return EventPrediction(
      homeWinProbability: (winProbability['home_win_probability'] as num).toDouble(),
      homeWinProbabilityModelVersion: winProbability['model_version'] as int,
      margin: ((predictions['margin'] as Map<String, dynamic>)['value'] as num).toDouble(),
      homeScore: ((predictions['home_score'] as Map<String, dynamic>)['value'] as num).toDouble(),
      awayScore: ((predictions['away_score'] as Map<String, dynamic>)['value'] as num).toDouble(),
      leaders: json['leaders'] != null ? EventLeaders.fromJson(json['leaders'] as Map<String, dynamic>) : null,
      stale: json['stale'] as bool? ?? false,
      staleRetryAfterSeconds: json['retry_after_seconds'] as int?,
    );
  }
}
