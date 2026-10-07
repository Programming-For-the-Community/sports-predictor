import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../core/theme/app_colors.dart';

/// How confident a win pick is, strongest first -- design/FRONTEND_STYLE.md's
/// tiers. Each starts at the favorite's win probability given here, and these
/// are the only place the app sets them. The backend grades the Performance
/// tab's confidence bands on the same figures
/// (library/performance/scorecard.py's WIN_PICK_FLOORS), and its tests fail
/// when the two differ.
enum ConfidenceTier {
  high('HIGH', 5 / 6, AppColors.cyan),
  med('MED', 2 / 3, AppColors.warn),
  low('LOW', 0.5, AppColors.inkMute);

  const ConfidenceTier(this.label, this.minProbability, this.color);

  final String label;

  /// The favorite's smallest win probability that reaches this tier.
  final double minProbability;
  final Color color;

  static ConfidenceTier forProbability(double homeWinProbability) {
    final favorite = math.max(homeWinProbability, 1 - homeWinProbability);
    return values.firstWhere((tier) => favorite >= tier.minProbability);
  }
}
