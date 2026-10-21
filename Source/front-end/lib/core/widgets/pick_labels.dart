import 'package:flutter/material.dart';

import '../models/upset.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// "DET -6.5": the side expected (or shown) to win and by how much, written
/// as a betting line. A whole-number margin drops its decimal when [trim] is
/// set, for a real final margin.
String lineLabel(String abbr, double margin, {bool trim = false}) {
  final size = margin.abs();
  final text = trim && size == size.roundToDouble() ? size.toStringAsFixed(0) : size.toStringAsFixed(1);
  return '$abbr -$text';
}

/// The colour key every actual-against-model screen carries once: white is
/// what happened, cyan is the model.
class ActualModelKey extends StatelessWidget {
  const ActualModelKey({super.key});

  static const actualLabel = 'ACTUAL';
  static const modelLabel = 'MODEL';

  Widget _entry(String label, Color color) => Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(width: 8, height: 8, decoration: BoxDecoration(color: color, borderRadius: BorderRadius.circular(2))),
          const SizedBox(width: 6),
          Text(label, style: AppTextStyles.microLabel(color: AppColors.inkSub)),
        ],
      );

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 14,
      runSpacing: 4,
      children: [_entry(actualLabel, AppColors.ink), _entry(modelLabel, AppColors.cyan)],
    );
  }
}

/// UPSET (amber) or UPSET WATCH, outlined so it never reads as a filled
/// confidence or verdict pill.
class UpsetChip extends StatelessWidget {
  const UpsetChip({super.key, required this.flag});

  final UpsetFlag flag;

  @override
  Widget build(BuildContext context) {
    final color = flag == UpsetFlag.upset ? AppColors.warn : AppColors.inkMid;
    final border = flag == UpsetFlag.upset ? AppColors.warn : AppColors.inkSub.withValues(alpha: 0.5);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
      decoration: BoxDecoration(border: Border.all(color: border), borderRadius: BorderRadius.circular(999)),
      child: Text(flag.label, style: AppTextStyles.microLabel(color: color)),
    );
  }
}

/// HIT or MISS for a finished game's winner pick.
class VerdictPill extends StatelessWidget {
  const VerdictPill({super.key, required this.correct});

  final bool correct;

  static const hitLabel = 'HIT';
  static const missLabel = 'MISS';

  @override
  Widget build(BuildContext context) {
    final color = correct ? AppColors.live : AppColors.neg;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(color: color.withValues(alpha: 0.12), borderRadius: BorderRadius.circular(999)),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(correct ? Icons.check_circle : Icons.cancel, color: color, size: 12),
          const SizedBox(width: 5),
          Text(correct ? hitLabel : missLabel, style: AppTextStyles.microLabel(color: color)),
        ],
      ),
    );
  }
}

/// A number that shrinks to fit its fixed-width slot instead of clipping,
/// right-aligned so a column of them lines up.
class SlotNumber extends StatelessWidget {
  const SlotNumber(this.text, {super.key, required this.width, required this.style});

  final String text;
  final double width;
  final TextStyle style;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: width,
      child: FittedBox(
        fit: BoxFit.scaleDown,
        alignment: Alignment.centerRight,
        child: Text(text, style: style, maxLines: 1),
      ),
    );
  }
}
