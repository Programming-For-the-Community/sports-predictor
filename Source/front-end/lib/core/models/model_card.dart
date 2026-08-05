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
/// this run. Carries TWO numbers, deliberately not one: `score` is the
/// same human-readable metric as the card's own top-level accuracy/mae
/// (easy to eyeball, but NOT what decided the ranking), while `rankScore`
/// is the value of whatever ModelCard.candidatesRankedBy names (log_loss/
/// rmse -- always lower-is-better) for this candidate specifically. The
/// two can disagree in ranking direction -- a candidate with the highest
/// `score` isn't guaranteed to be first in ModelCard.candidates, because a
/// better raw accuracy doesn't always mean better-calibrated
/// probabilities. Confirmed confusing in practice without rankScore
/// visible: a real run promoted xgboost over a candidate with higher raw
/// accuracy, because xgboost had the better log_loss -- correct, but
/// looked like a bug when only `score` was shown. `rankScore` is nullable
/// since it's absent on any candidate list written before this field
/// existed (older cards only ever had `score`).
class ModelCandidate {
  const ModelCandidate({required this.algorithm, required this.score, required this.rankScore});

  final String algorithm;
  final double score;
  final double? rankScore;

  factory ModelCandidate.fromJson(Map<String, dynamic> json) => ModelCandidate(
        algorithm: json['algorithm'] as String,
        score: (json['score'] as num).toDouble(),
        rankScore: (json['rank_score'] as num?)?.toDouble(),
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
    required this.candidatesRankedBy,
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
  // Which metric each candidate's rankScore is ("log_loss"/"rmse") -- see
  // ModelCandidate's own docs for why this needs to be visible at all.
  // Null alongside candidates on any card predating this field.
  final String? candidatesRankedBy;

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
        candidatesRankedBy: json['candidates_ranked_by'] as String?,
      );
}
