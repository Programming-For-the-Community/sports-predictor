import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// Static "FINAL" pill for a completed event -- same pill/dotOnly
/// convention as LiveStatusPill/ConfidencePill/field_status_pill.dart's
/// own dotOnly. Muted (not live-green) since the game itself is over,
/// not in progress.
class FinalStatusPill extends StatelessWidget {
  const FinalStatusPill({super.key, this.dotOnly = false});

  // True on a narrow (mobile) viewport -- collapses to just the colored
  // dot (with "FINAL" moved into a Tooltip instead).
  final bool dotOnly;

  @override
  Widget build(BuildContext context) {
    if (dotOnly) {
      return const Tooltip(
        message: 'FINAL',
        child: SizedBox(
          width: 8,
          height: 8,
          child: DecoratedBox(decoration: BoxDecoration(color: AppColors.inkMute, shape: BoxShape.circle)),
        ),
      );
    }
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(color: AppColors.inkMute.withValues(alpha: 0.15), borderRadius: BorderRadius.circular(999)),
      child: Text('FINAL', style: AppTextStyles.microLabel(color: AppColors.inkMute)),
    );
  }
}
