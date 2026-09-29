import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// The shared shell of a model card: the raised surface, the model's title,
/// and a row of small badges. The Models tab (training info) and the
/// Performance tab (season/last-period results) both fill `children` with
/// their own content, so the two always look like one family of cards.
class ModelCardFrame extends StatelessWidget {
  const ModelCardFrame({super.key, required this.title, required this.badges, required this.children, this.footer});

  final String title;
  final List<String> badges;
  final List<Widget> children;

  /// Full-width content under `children`. When the card is stretched to a
  /// taller row (ModelCardGrid's equal-height rows), the spare space goes
  /// above it, so footers line up along the bottom of the row.
  final Widget? footer;

  static const _footerGap = 20.0;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: AppColors.border),
      ),
      child: LayoutBuilder(
        builder: (context, constraints) => Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Tooltip(message: title, child: Text(title, style: AppTextStyles.cardTitle())),
            const SizedBox(height: 8),
            Wrap(spacing: 8, runSpacing: 8, children: [for (final badge in badges) ModelCardBadge(text: badge)]),
            ...children,
            if (footer != null) ...[
              // Only a stretched card has a bounded height to fill.
              if (constraints.hasBoundedHeight) const Spacer(),
              const SizedBox(height: _footerGap),
              footer!,
            ],
          ],
        ),
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
