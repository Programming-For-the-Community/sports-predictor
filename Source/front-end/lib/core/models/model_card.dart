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

class ModelCard {
  const ModelCard({
    required this.modelName,
    required this.algorithm,
    required this.version,
    required this.trainedAt,
    required this.topFeatures,
    required this.accuracy,
    required this.logLoss,
    required this.rmse,
    required this.mae,
  });

  final String modelName;
  final String algorithm;
  final int version;
  final String trainedAt;
  final List<ModelFeatureImportance> topFeatures;

  // Present for the win-probability classifier.
  final double? accuracy;
  final double? logLoss;

  // Present for regressors (score-margin, home/away score, player props).
  final double? rmse;
  final double? mae;

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
        rmse: (json['rmse'] as num?)?.toDouble(),
        mae: (json['mae'] as num?)?.toDouble(),
      );
}
