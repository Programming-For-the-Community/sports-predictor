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
    // Flex-based, not fixed pixel widths -- a fixed SizedBox wide enough
    // for a long feature name (e.g. "away_coach_season_win_pct") either
    // overflows a narrow phone-width card or forces the card wider than
    // it should ever need to be just to fit that one row. Expanded
    // shares whatever width the card actually has proportionally, so
    // this scales down gracefully on a small screen instead of doing
    // either of those -- the Tooltip is what covers a name that still
    // doesn't fully fit at any given width, not a wider fixed column.
    return Row(
      children: [
        Expanded(
          flex: 3,
          child: Tooltip(
            message: feature.feature,
            child: Text(
              feature.feature,
              style: AppTextStyles.microLabel(color: AppColors.inkSub),
              overflow: TextOverflow.ellipsis,
              maxLines: 1,
            ),
          ),
        ),
        const SizedBox(width: 10),
        Expanded(
          flex: 4,
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
        Expanded(
          flex: 2,
          child: Text(
            feature.importance.toStringAsFixed(2),
            textAlign: TextAlign.right,
            overflow: TextOverflow.ellipsis,
            maxLines: 1,
            style: AppTextStyles.metricValue(color: AppColors.cyan),
          ),
        ),
      ],
    );
  }
}
