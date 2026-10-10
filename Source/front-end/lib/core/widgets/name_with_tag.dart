import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// A leaderboard row's name over one small line: a detail (country, team)
/// and a coloured tag (round status, DNF), either of which may be absent.
class NameWithTag extends StatelessWidget {
  const NameWithTag({super.key, required this.name, this.detail, this.tag, this.tagColor = AppColors.inkSub});

  final String name;
  final String? detail;
  final String? tag;
  final Color tagColor;

  @override
  Widget build(BuildContext context) {
    final detail = this.detail;
    final tag = this.tag;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(name, style: AppTextStyles.body(color: AppColors.ink)),
        if (detail != null || tag != null)
          Text.rich(
            TextSpan(
              style: AppTextStyles.microLabel(color: AppColors.inkSub),
              children: [
                if (detail != null) TextSpan(text: detail),
                if (detail != null && tag != null) const TextSpan(text: ' · '),
                if (tag != null) TextSpan(text: tag, style: AppTextStyles.microLabel(color: tagColor)),
              ],
            ),
          ),
      ],
    );
  }
}
