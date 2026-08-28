/// Mirrors GET /{sport}/models' response shape. `topFeatures` is already
/// sliced to 5 and sorted descending by gain server-side -- this model
/// never re-sorts or re-slices.
class ModelFeatureImportance {
  const ModelFeatureImportance({required this.feature, required this.importance});

  final String feature;
  final double importance;

  factory ModelFeatureImportance.fromJson(Map<String, dynamic> json) => ModelFeatureImportance(
        feature: json['feature'] as String,
        importance: (json['importance'] as num).toDouble(),
      );
}

/// One algorithm the backtesting harness tried for this target on this
/// run. Carries two numbers, deliberately not one: `score` is the same
/// human-readable metric as the card's own top-level accuracy/mae (easy to
/// eyeball, but not what decided the ranking), while `rankScore` is the
/// value of whatever ModelCard.candidatesRankedBy names (log_loss/rmse,
/// always lower-is-better) for this candidate specifically. The two can
/// disagree in ranking direction, since a better raw accuracy doesn't
/// always mean better-calibrated probabilities. `rankScore` is nullable
/// since older candidate lists only ever had `score`.
///
/// `score` is also nullable for a distinct reason: library/ml/backtest.py's
/// _full_candidate_summary writes a "not_evaluated" placeholder entry
/// (score/rank_score/training_seconds all null) for a candidate not yet
/// tried when a model card is captured mid-run. `status` surfaces that
/// ("not_evaluated" or null for an actually-evaluated candidate).
class ModelCandidate {
  const ModelCandidate({required this.algorithm, required this.score, required this.rankScore, this.status});

  final String algorithm;
  final double? score;
  final double? rankScore;
  final String? status;

  factory ModelCandidate.fromJson(Map<String, dynamic> json) => ModelCandidate(
        algorithm: json['algorithm'] as String,
        score: (json['score'] as num?)?.toDouble(),
        rankScore: (json['rank_score'] as num?)?.toDouble(),
        status: json['status'] as String?,
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
  // log_loss. Absent on older model cards.
  final double? naiveBaselineAccuracy;

  // Present for regressors (score-margin, home/away score, player props).
  final double? rmse;
  final double? mae;
  // mae of predicting this player's/team's own rolling average -- same
  // baseline-skill role as naiveBaselineAccuracy above, just for
  // regressors. Absent on older model cards.
  final double? naiveBaselineMae;

  // Every algorithm this run's tournament tried, including this card's
  // own. Null (not empty) when no backtesting harness ran, distinct from
  // a single-candidate run.
  final List<ModelCandidate>? candidates;
  // Which metric each candidate's rankScore is ("log_loss"/"rmse"). Null
  // alongside candidates.
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
