import 'package:flutter/material.dart';

import '../models/model_card.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// Adapted from design/FRONTEND_STYLE.md's "Feature attribution" component.
/// That spec describes SIGNED per-prediction contributions (diverging
/// left/right from a centerline); what the model card actually carries is
/// unsigned training-time gain (see Source/model-training/nfl/model_common.py,
/// XGBoost's get_score(importance_type="gain")) -- there's no sign to
/// diverge on. This keeps the same cyan-fill visual language as a ranked
/// bar chart instead of fabricating a sign that isn't in the data.
class FeatureAttributionBars extends StatelessWidget {
  const FeatureAttributionBars({super.key, required this.features});

  final List<ModelFeatureImportance> features;

  @override
  Widget build(BuildContext context) {
    if (features.isEmpty) {
      return Text('No feature importance recorded.', style: AppTextStyles.body(color: AppColors.inkMute));
    }
    final maxImportance = features.map((f) => f.importance).reduce((a, b) => a > b ? a : b);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final feature in features) ...[
          _FeatureBar(feature: feature, fraction: maxImportance == 0 ? 0 : feature.importance / maxImportance),
          const SizedBox(height: 10),
        ],
      ],
    );
  }
}

class _FeatureBar extends StatelessWidget {
  const _FeatureBar({required this.feature, required this.fraction});

  final ModelFeatureImportance feature;
  final double fraction;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        SizedBox(
          width: 160,
          child: Text(
            feature.feature,
            style: AppTextStyles.microLabel(color: AppColors.inkSub),
            overflow: TextOverflow.ellipsis,
          ),
        ),
        Expanded(
          child: ClipRRect(
            borderRadius: BorderRadius.circular(999),
            child: SizedBox(
              height: 8,
              child: Stack(
                children: [
                  Container(color: const Color(0xFF1a2233)),
                  FractionallySizedBox(
                    widthFactor: fraction.clamp(0.02, 1.0),
                    child: DecoratedBox(decoration: BoxDecoration(gradient: AppColors.cyanFill)),
                  ),
                ],
              ),
            ),
          ),
        ),
        const SizedBox(width: 10),
        SizedBox(
          width: 56,
          child: Text(
            feature.importance.toStringAsFixed(2),
            textAlign: TextAlign.right,
            style: AppTextStyles.metricValue(color: AppColors.cyan),
          ),
        ),
      ],
    );
  }
}
