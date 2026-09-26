import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// The shared shell of a model card: the raised surface, the model's title,
/// and a row of small badges. The Models tab (training info) and the
/// Performance tab (season/last-period results) both fill `children` with
/// their own content, so the two always look like one family of cards.
class ModelCardFrame extends StatelessWidget {
  const ModelCardFrame({super.key, required this.title, required this.badges, required this.children});

  final String title;
  final List<String> badges;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Tooltip(message: title, child: Text(title, style: AppTextStyles.cardTitle())),
          const SizedBox(height: 8),
          Wrap(spacing: 8, runSpacing: 8, children: [for (final badge in badges) ModelCardBadge(text: badge)]),
          ...children,
        ],
      ),
    );
  }
}

class ModelCardBadge extends StatelessWidget {
  const ModelCardBadge({super.key, required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(color: AppColors.inset, borderRadius: BorderRadius.circular(999)),
      child: Text(text, style: AppTextStyles.microLabel()),
    );
  }
}
