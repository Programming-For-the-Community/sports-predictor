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
          // Title on its own full-width line(s), badges wrapped below.
          // Tooltip is a safety net for a name that doesn't fit maxLines: 2.
          Tooltip(
            message: _displayName(model.modelName),
            child: Text(
              _displayName(model.modelName),
              style: AppTextStyles.cardTitle(),
              overflow: TextOverflow.ellipsis,
              maxLines: 2,
            ),
          ),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              _Badge(text: model.algorithm.toUpperCase()),
              _Badge(text: 'v${model.version}'),
            ],
          ),
          const SizedBox(height: 18),
          // Wrap, not a plain Row -- a wide value string could overflow an
          // unconstrained Row on a narrow phone-width card.
          Wrap(
            spacing: 32,
            runSpacing: 12,
            children: [
              for (final metric in _metrics()) _MetricStat(label: metric.$1, value: metric.$2),
            ],
          ),
          const SizedBox(height: 20),
          Text('TOP FEATURES', style: AppTextStyles.microLabel()),
          const SizedBox(height: 12),
          FeatureAttributionBars(features: model.topFeatures),
          if ((model.candidates?.length ?? 0) > 1) ...[
            const SizedBox(height: 20),
            Text('COMPARED AGAINST', style: AppTextStyles.microLabel()),
            const SizedBox(height: 8),
            _CandidateComparison(
              candidates: model.candidates!,
              currentAlgorithm: model.algorithm,
              isClassifier: model.isClassifier,
              unit: _unitLabel(model.modelName),
            ),
          ],
        ],
      ),
    );
  }

  String _displayName(String modelName) => modelName.split('-').map((w) => w[0].toUpperCase() + w.substring(1)).join(' ');

  // Shows skill relative to a trivial baseline (always pick the home team;
  // predict the player's own rolling average) rather than a raw
  // log_loss/rmse number, which doesn't say whether that's good.
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

  /// Unit for a regressor candidate's +/- value in the comparison list
  /// (e.g. "±7.4 YDS"). Pattern-matched on modelName rather than an
  /// exhaustive per-stat switch.
  String _unitLabel(String modelName) {
    if (modelName.contains('yards')) return 'YDS';
    if (modelName.contains('touchdowns')) return 'TDS';
    if (modelName.contains('sacks')) return 'SACKS';
    return 'PTS'; // score-margin / home-score / away-score
  }

  /// Always "+X% BETTER" (or "-X% WORSE") -- how much better the actual
  /// value is than the baseline, as a fraction of the baseline itself.
  /// '--' if either value is missing.
  String _vsBaseline(double? actual, double? baseline, {required bool higherIsBetter}) {
    if (actual == null || baseline == null || baseline == 0) return '--';
    final relativeChange = higherIsBetter
        ? (actual - baseline) / baseline * 100 // classifier: relative lift in accuracy
        : (baseline - actual) / baseline * 100; // regressor: relative reduction in error
    final label = relativeChange >= 0 ? 'BETTER' : 'WORSE';
    return '${relativeChange >= 0 ? '+' : ''}${relativeChange.toStringAsFixed(0)}% $label';
  }
}

/// Every algorithm the backtesting harness tried for this target, already
/// ranked best-first server-side. Each candidate's `score` is the
/// human-readable metric (accuracy or mae, never log_loss/rmse).
class _CandidateComparison extends StatelessWidget {
  const _CandidateComparison({
    required this.candidates, required this.currentAlgorithm, required this.isClassifier, required this.unit,
  });

  final List<ModelCandidate> candidates;
  final String currentAlgorithm;
  final bool isClassifier;
  final String unit;

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
            value: _candidateValue(candidate),
            isCurrent: candidate.algorithm == currentAlgorithm,
          ),
          const SizedBox(height: 8),
        ],
      ],
    );
  }

  /// "Not yet evaluated" for a candidate library/ml/backtest.py's
  /// _full_candidate_summary placeholder-recorded (score null, status
  /// "not_evaluated") -- a real shape, not missing/malformed data.
  String _candidateValue(ModelCandidate candidate) {
    final score = candidate.score;
    if (score == null) return 'Not yet evaluated';
    return isClassifier ? '${(score * 100).toStringAsFixed(1)}%' : '±${score.toStringAsFixed(1)} $unit';
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
