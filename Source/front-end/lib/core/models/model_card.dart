/// Mirrors GET /{sport}/models' response shape (see
/// Source/aws-lambdas/nfl/predict/handler.py's _list_models). `topFeatures`
/// is already sliced to 5 and sorted descending by gain server-side --
/// this model never re-sorts or re-slices.
class ModelFeatureImportance {
  const ModelFeatureImportance({required this.feature, required this.importance});

  final String feature;
  final double importance;

  factory ModelFeatureImportance.fromJson(Map<String, dynamic> json) => ModelFeatureImportance(
        feature: json['feature'] as String,
        importance: (json['importance'] as num).toDouble(),
      );
}

/// One algorithm library.ml.backtest.run_backtest tried for this target on
/// this run, and its own score on whatever metric this card's own gate
/// metric is (log_loss for a classifier, rmse for a regressor) -- lower is
/// always better, regardless of task. `score` is a fixed key name on the
/// wire precisely so this model doesn't need to know which metric it is.
class ModelCandidate {
  const ModelCandidate({required this.algorithm, required this.score});

  final String algorithm;
  final double score;

  factory ModelCandidate.fromJson(Map<String, dynamic> json) => ModelCandidate(
        algorithm: json['algorithm'] as String,
        score: (json['score'] as num).toDouble(),
      );
}

class ModelCard {
  const ModelCard({
    required this.modelName,
    required this.algorithm,
    required this.version,
    required this.trainedAt,
    required this.topFeatures,
    required this.accuracy,
    required this.logLoss,
    required this.naiveBaselineAccuracy,
    required this.rmse,
    required this.mae,
    required this.naiveBaselineMae,
    required this.candidates,
  });

  final String modelName;
  final String algorithm;
  final int version;
  final String trainedAt;
  final List<ModelFeatureImportance> topFeatures;

  // Present for the win-probability classifier.
  final double? accuracy;
  final double? logLoss;
  // Accuracy of always picking the home team -- lets the UI show skill
  // relative to a trivial baseline instead of the much less intuitive
  // log_loss (see model_card_view.dart). Absent on model cards trained
  // before this field was added.
  final double? naiveBaselineAccuracy;

  // Present for regressors (score-margin, home/away score, player props).
  final double? rmse;
  final double? mae;
  // mae of predicting this player's/team's own rolling average -- same
  // baseline-skill role as naiveBaselineAccuracy above, just for
  // regressors. Absent on model cards trained before this field was added.
  final double? naiveBaselineMae;

  // Every algorithm this run's tournament tried, including this card's own
  // -- null (not empty) on any model card trained before the backtesting
  // harness existed, distinct from a hypothetical single-candidate run.
  final List<ModelCandidate>? candidates;

  bool get isClassifier => accuracy != null;

  factory ModelCard.fromJson(Map<String, dynamic> json) => ModelCard(
        modelName: json['model_name'] as String,
        algorithm: json['algorithm'] as String,
        version: json['version'] as int,
        trainedAt: json['trained_at'] as String,
        topFeatures: (json['top_features'] as List<dynamic>? ?? [])
            .map((f) => ModelFeatureImportance.fromJson(f as Map<String, dynamic>))
            .toList(),
        accuracy: (json['accuracy'] as num?)?.toDouble(),
        logLoss: (json['log_loss'] as num?)?.toDouble(),
        naiveBaselineAccuracy: (json['naive_baseline_accuracy'] as num?)?.toDouble(),
        rmse: (json['rmse'] as num?)?.toDouble(),
        mae: (json['mae'] as num?)?.toDouble(),
        naiveBaselineMae: (json['naive_baseline_mae'] as num?)?.toDouble(),
        candidates: (json['candidates'] as List<dynamic>?)
            ?.map((c) => ModelCandidate.fromJson(c as Map<String, dynamic>))
            .toList(),
      );
}
