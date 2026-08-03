import 'event_leaders.dart';

/// Mirrors GET /{sport}/predictions/events/{event_id}'s response shape
/// (see Source/aws-lambdas/nfl/predict/handler.py's _predict_event).
/// `leaders` is optional -- planned (see event_leaders.dart) but not yet
/// returned by the currently-deployed API.
class EventPrediction {
  const EventPrediction({
    required this.homeWinProbability,
    required this.homeWinProbabilityModelVersion,
    required this.margin,
    required this.homeScore,
    required this.awayScore,
    required this.leaders,
  });

  final double homeWinProbability;
  final int homeWinProbabilityModelVersion;
  final double margin;
  final double homeScore;
  final double awayScore;
  final EventLeaders? leaders;

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
    );
  }
}
