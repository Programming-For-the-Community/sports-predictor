import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// "SPRINT" pill for an F1 sprint-format event -- shared by f1_event_row.
/// dart (list row) and f1_event_detail_page.dart (detail header), which
/// previously each built an identical Container/Text independently.
class SprintBadge extends StatelessWidget {
  const SprintBadge({super.key});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(color: AppColors.cyan.withValues(alpha: 0.15), borderRadius: BorderRadius.circular(999)),
      child: Text('SPRINT', style: AppTextStyles.microLabel(color: AppColors.cyan)),
    );
  }
}
