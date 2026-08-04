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
        ],
      ),
    );
  }

  String _displayName(String modelName) => modelName.split('-').map((w) => w[0].toUpperCase() + w.substring(1)).join(' ');

  List<(String, String)> _metrics() {
    if (model.isClassifier) {
      return [
        ('ACCURACY', '${(model.accuracy! * 100).toStringAsFixed(1)}%'),
        ('LOG LOSS', model.logLoss!.toStringAsFixed(3)),
      ];
    }
    return [
      ('RMSE', model.rmse?.toStringAsFixed(2) ?? '--'),
      ('MAE', model.mae?.toStringAsFixed(2) ?? '--'),
    ];
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
