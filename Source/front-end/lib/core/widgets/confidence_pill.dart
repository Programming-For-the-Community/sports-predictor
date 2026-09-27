import 'package:flutter/material.dart';

import '../../static/confidence_tiers.dart';
import '../theme/app_text_styles.dart';

/// A win pick's ConfidenceTier as a colored pill. Win-probability only -- margin/score/player-prop predictions are plain
/// regression point estimates with no probability distribution to derive
/// a tier from.
class ConfidencePill extends StatelessWidget {
  const ConfidencePill({super.key, required this.homeWinProbability, this.dotOnly = false});

  final double homeWinProbability;
  // True on a narrow (mobile) viewport -- collapses to just the
  // color-coded dot (tier name moves into a Tooltip instead), same
  // space-saving convention field_status_pill.dart's own dotOnly uses.
  final bool dotOnly;

  @override
  Widget build(BuildContext context) {
    final tier = ConfidenceTier.forProbability(homeWinProbability);
    final (label, color) = (tier.label, tier.color);

    if (dotOnly) {
      return Tooltip(
        message: label,
        child: Container(width: 8, height: 8, decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
      );
    }

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(label, style: AppTextStyles.microLabel(color: color)),
    );
  }
}
