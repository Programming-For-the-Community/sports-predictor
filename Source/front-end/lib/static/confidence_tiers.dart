import 'package:flutter/material.dart';

import '../core/theme/app_colors.dart';

/// How confident a win pick is, by its edge over a coin flip
/// (|home win probability - 0.5|), strongest first -- design/FRONTEND_STYLE.md's
/// tiers. The backend grades the Performance tab's confidence bands on the
/// same edges (library/performance/scorecard.py's WIN_PICK_TIERS).
enum ConfidenceTier {
  high('HIGH', 0.13, AppColors.cyan),
  med('MED', 0.06, AppColors.warn),
  low('LOW', 0.0, AppColors.inkMute);

  const ConfidenceTier(this.label, this.minEdge, this.color);

  final String label;

  /// The smallest edge over 50/50 that reaches this tier.
  final double minEdge;
  final Color color;

  static ConfidenceTier forProbability(double homeWinProbability) {
    final edge = (homeWinProbability - 0.5).abs();
    return values.firstWhere((tier) => edge >= tier.minEdge);
  }
}
