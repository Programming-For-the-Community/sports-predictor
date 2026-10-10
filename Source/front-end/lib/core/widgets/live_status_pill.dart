import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// "LIVE" pill with its green dot, shared by every list row and event page.
class LiveStatusPill extends StatelessWidget {
  const LiveStatusPill({super.key});

  // sport_card.dart's status pill shows this word too, without the dot.
  static const label = 'LIVE';

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(color: AppColors.live.withValues(alpha: 0.15), borderRadius: BorderRadius.circular(999)),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(width: 6, height: 6, decoration: const BoxDecoration(shape: BoxShape.circle, color: AppColors.live)),
          const SizedBox(width: 6),
          Text(label, style: AppTextStyles.microLabel(color: AppColors.live)),
        ],
      ),
    );
  }
}
