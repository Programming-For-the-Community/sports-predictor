import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// "FINAL" pill for a finished event -- muted, not live-green, since the
/// game is over.
class FinalStatusPill extends StatelessWidget {
  const FinalStatusPill({super.key});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(color: AppColors.inkMute.withValues(alpha: 0.15), borderRadius: BorderRadius.circular(999)),
      child: Text('FINAL', style: AppTextStyles.microLabel(color: AppColors.inkMute)),
    );
  }
}
