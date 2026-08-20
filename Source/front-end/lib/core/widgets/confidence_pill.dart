import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// design/FRONTEND_STYLE.md's confidence tiers: "distance off 50/50: edge
/// >= 0.13 -> HIGH (cyan), >= 0.06 -> MED (amber), else LOW (muted)".
/// Win-probability only -- margin/score/player-prop predictions are plain
/// regression point estimates with no probability distribution to derive
/// a tier from.
class ConfidencePill extends StatelessWidget {
  const ConfidencePill({super.key, required this.homeWinProbability});

  final double homeWinProbability;

  @override
  Widget build(BuildContext context) {
    final edge = (homeWinProbability - 0.5).abs();
    final (label, color) = edge >= 0.13
        ? ('HIGH', AppColors.cyan)
        : edge >= 0.06
            ? ('MED', AppColors.warn)
            : ('LOW', AppColors.inkMute);

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
