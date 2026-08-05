import 'package:flutter/material.dart';

import '../models/model_card.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';
import 'feature_attribution_bars.dart';

/// design/FRONTEND_STYLE.md's "Model card" component -- quiet --surface
/// card showing what's actually driving this model's predictions:
/// algorithm, accuracy, and its top features.
class ModelCardView extends StatelessWidget {
  const ModelCardView({super.key, required this.model});

  final ModelCard model;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  _displayName(model.modelName),
                  style: AppTextStyles.cardTitle(),
                  overflow: TextOverflow.ellipsis,
                  maxLines: 1,
                ),
              ),
              const SizedBox(width: 8),
              _Badge(text: model.algorithm.toUpperCase()),
              const SizedBox(width: 8),
              _Badge(text: 'v${model.version}'),
            ],
          ),
          const SizedBox(height: 18),
          Row(
            children: [
              for (final metric in _metrics()) ...[
                _MetricStat(label: metric.$1, value: metric.$2),
                const SizedBox(width: 32),
              ],
            ],
          ),
          const SizedBox(height: 20),
          Text('TOP FEATURES', style: AppTextStyles.microLabel()),
          const SizedBox(height: 12),
          FeatureAttributionBars(features: model.topFeatures),
          if ((model.candidates?.length ?? 0) > 1) ...[
            const SizedBox(height: 20),
            Text('COMPARED AGAINST', style: AppTextStyles.microLabel()),
            const SizedBox(height: 4),
            // Candidates are ranked by rank_score (log_loss/rmse), not by
            // the score value shown per row -- without this line, a
            // candidate with a higher displayed score sitting below the
            // promoted one looks like a bug rather than a real, common
            // outcome (better raw accuracy doesn't always mean
            // better-calibrated probabilities). See ModelCandidate's own
            // docs in model_card.dart.
            Text(
              'Ranked by ${_metricLabel(model.candidatesRankedBy)}, not the value shown',
              style: AppTextStyles.body(color: AppColors.inkMute),
            ),
            const SizedBox(height: 8),
            _CandidateComparison(candidates: model.candidates!, currentAlgorithm: model.algorithm, isClassifier: model.isClassifier),
          ],
        ],
      ),
    );
  }

  String _displayName(String modelName) => modelName.split('-').map((w) => w[0].toUpperCase() + w.substring(1)).join(' ');

  // log_loss/rmse are real evaluation metrics, but neither means anything
  // to someone without an ML background -- "log loss 0.65" and "RMSE 9.8"
  // don't say whether that's good. Showing skill relative to a trivial
  // baseline (always pick the home team; predict the player's own rolling
  // average) answers the question a viewer actually has: "is this model
  // better than just guessing?" -- see naive_baseline_accuracy/
  // naive_baseline_mae's docs in model_card.dart.
  List<(String, String)> _metrics() {
    if (model.isClassifier) {
      return [
        ('ACCURACY', '${(model.accuracy! * 100).toStringAsFixed(1)}%'),
        ('VS BASELINE', _vsBaseline(model.accuracy, model.naiveBaselineAccuracy, higherIsBetter: true)),
      ];
    }
    return [
      ('AVG MISS', model.mae?.toStringAsFixed(1) ?? '--'),
      ('VS BASELINE', _vsBaseline(model.mae, model.naiveBaselineMae, higherIsBetter: false)),
    ];
  }

  /// "log loss" / "rmse" -- human-cased for the caption above the
  /// candidate list. Falls back to a generic phrase for any card
  /// predating candidatesRankedBy, or an unrecognized metric name (new
  /// gate metrics land in code before they'd ever reach this UI, so this
  /// is a defensive fallback, not an expected path).
  String _metricLabel(String? metricKey) {
    switch (metricKey) {
      case 'log_loss':
        return 'log loss';
      case 'rmse':
        return 'RMSE';
      default:
        return 'a different metric';
    }
  }

  /// "+6.2 PTS" (classifier: percentage-point lift over always picking the
  /// home team) or "23% BETTER" (regressor: relative reduction in average
  /// miss versus predicting the rolling average) -- '--' if either value
  /// is missing, which happens for any model card trained before these
  /// baseline fields existed.
  String _vsBaseline(double? actual, double? baseline, {required bool higherIsBetter}) {
    if (actual == null || baseline == null || baseline == 0) return '--';
    if (higherIsBetter) {
      final points = (actual - baseline) * 100;
      return '${points >= 0 ? '+' : ''}${points.toStringAsFixed(1)} PTS';
    }
    final improvement = (baseline - actual) / baseline * 100;
    return '${improvement >= 0 ? '+' : ''}${improvement.toStringAsFixed(0)}% BETTER';
  }
}

/// Every algorithm the backtesting harness (library/ml/backtest.py) tried
/// for this target, already ranked best-first server-side. Each
/// candidate's `score` is deliberately already the human-readable metric
/// (accuracy or mae, never log_loss/rmse) -- the model card's raw metrics
/// still carry the real evaluation numbers for anyone who wants them
/// (see model_card.dart), this widget just never renders those directly.
class _CandidateComparison extends StatelessWidget {
  const _CandidateComparison({required this.candidates, required this.currentAlgorithm, required this.isClassifier});

  final List<ModelCandidate> candidates;
  final String currentAlgorithm;
  final bool isClassifier;

  static const _algorithmLabels = {
    'xgboost': 'XGBoost',
    'logistic_regression': 'Logistic Regression',
    'elastic_net': 'ElasticNet',
    'random_forest_classifier': 'Random Forest',
    'random_forest_regressor': 'Random Forest',
    'mlp_classifier': 'Neural Net',
    'mlp_regressor': 'Neural Net',
  };

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final candidate in candidates) ...[
          _CandidateRow(
            label: _algorithmLabels[candidate.algorithm] ?? candidate.algorithm,
            value: isClassifier ? '${(candidate.score * 100).toStringAsFixed(1)}%' : '±${candidate.score.toStringAsFixed(1)}',
            isCurrent: candidate.algorithm == currentAlgorithm,
          ),
          const SizedBox(height: 8),
        ],
      ],
    );
  }
}

class _CandidateRow extends StatelessWidget {
  const _CandidateRow({required this.label, required this.value, required this.isCurrent});

  final String label;
  final String value;
  final bool isCurrent;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(
          child: Text(
            label,
            style: AppTextStyles.body(color: isCurrent ? AppColors.ink : AppColors.inkMute),
            overflow: TextOverflow.ellipsis,
          ),
        ),
        if (isCurrent) ...[
          _Badge(text: 'PROMOTED'),
          const SizedBox(width: 10),
        ],
        Text(value, style: AppTextStyles.metricValue(color: isCurrent ? AppColors.cyan : AppColors.inkSub)),
      ],
    );
  }
}

class _Badge extends StatelessWidget {
  const _Badge({required this.text});
  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(color: AppColors.inset, borderRadius: BorderRadius.circular(999)),
      child: Text(text, style: AppTextStyles.microLabel()),
    );
  }
}

class _MetricStat extends StatelessWidget {
  const _MetricStat({required this.label, required this.value});
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: AppTextStyles.microLabel()),
        const SizedBox(height: 4),
        Text(value, style: AppTextStyles.metricValueLarge(color: AppColors.cyan)),
      ],
    );
  }
}
