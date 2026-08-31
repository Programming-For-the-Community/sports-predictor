import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// One driver's own race/qualifying status, as a small colored pill --
/// map_status vocabulary (library/normalize/f1.py): finished/classified/
/// dnf/dsq/dns. A separate file from field_status_pill.dart (PGA's own
/// status vocabulary is a genuinely different set of strings -- reusing
/// it here would mis-color a real "classified" or "dnf" result as if the
/// driver were still racing, since neither string is in PGA's own
/// recognized set) -- same "parallel files, don't generalize the shared
/// one" precedent field_prediction_computing_retry.dart's own doc
/// comment already establishes.
class F1StatusPill extends StatelessWidget {
  const F1StatusPill({super.key, required this.status, this.dotOnly = false});

  final String? status;
  // True on a narrow (compact) viewport -- collapses to just the colored
  // dot, keeping the color-coded signal without text.
  final bool dotOnly;

  @override
  Widget build(BuildContext context) {
    final (label, color) = switch (status) {
      'finished' => ('Finished', AppColors.inkSub),
      // Finished but didn't cover the real >=90%-distance classification
      // threshold -- still counts (points/finish_position are real), so
      // this is NOT a negative color the way dnf/dsq are.
      'classified' => ('Classified', AppColors.inkSub),
      'dnf' => ('DNF', AppColors.neg),
      'dsq' => ('DSQ', AppColors.neg),
      'dns' => ('DNS', AppColors.warn),
      null => ('--', AppColors.inkMute),
      // Unrecognized -- treat as still racing/unresolved rather than
      // guessing at a negative outcome.
      _ => (status!.toUpperCase(), AppColors.live),
    };

    if (dotOnly) {
      return Tooltip(
        message: label,
        child: Container(width: 8, height: 8, decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
      );
    }

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(color: color.withValues(alpha: 0.15), borderRadius: BorderRadius.circular(999)),
      child: Text(label, style: AppTextStyles.microLabel(color: color), maxLines: 1, softWrap: false, overflow: TextOverflow.ellipsis),
    );
  }
}
