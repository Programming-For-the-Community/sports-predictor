import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// Pulsing-dot "LIVE" pill, shared by game_row.dart and matchup_hero.dart.
class LiveStatusPill extends StatelessWidget {
  const LiveStatusPill({super.key, this.dotOnly = false});

  // sport_card.dart's own private _StatusPill also shows this exact word
  // (its LIVE state has no pulsing dot, so it doesn't just compose this
  // widget) -- referenced from there instead of retyped.
  static const label = 'LIVE';

  // True on a narrow (mobile) viewport -- collapses to just the colored
  // dot (with "LIVE" moved into a Tooltip instead), same space-saving
  // convention field_status_pill.dart's own dotOnly uses.
  final bool dotOnly;

  @override
  Widget build(BuildContext context) {
    if (dotOnly) {
      return const Tooltip(
        message: label,
        child: SizedBox(
          width: 8,
          height: 8,
          child: DecoratedBox(decoration: BoxDecoration(color: AppColors.live, shape: BoxShape.circle)),
        ),
      );
    }
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(color: AppColors.live.withValues(alpha: 0.15), borderRadius: BorderRadius.circular(999)),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(width: 6, height: 6, decoration: BoxDecoration(shape: BoxShape.circle, color: AppColors.live)),
          const SizedBox(width: 6),
          Text(label, style: AppTextStyles.microLabel(color: AppColors.live)),
        ],
      ),
    );
  }
}
