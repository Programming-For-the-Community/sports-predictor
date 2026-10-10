import 'package:flutter/material.dart';

import '../../static/confidence_tiers.dart';
import '../theme/app_text_styles.dart';

/// A win pick's ConfidenceTier as a colored pill, its tier always spelled
/// out. Win-probability only -- margin/score/player-prop predictions are
/// plain point estimates with no probability to derive a tier from.
class ConfidencePill extends StatelessWidget {
  const ConfidencePill({super.key, required this.homeWinProbability});

  final double homeWinProbability;

  @override
  Widget build(BuildContext context) {
    final tier = ConfidenceTier.forProbability(homeWinProbability);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: tier.color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(tier.label, style: AppTextStyles.microLabel(color: tier.color)),
    );
  }
}
