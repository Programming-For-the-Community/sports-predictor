import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// One golfer's own round/tournament status, as a small colored pill --
/// map_status vocabulary (library/normalize/pga.py): scheduled/finished/
/// cut/made_cut_did_not_finish/withdrawn, plus whatever ESPN's real
/// in-progress status name turns out to be (never confirmed live as of
/// this writing -- see project-pga-onboarding memory) or any other
/// unrecognized value, both of which fall through to the "still playing"
/// branch below rather than being silently mislabeled.
class FieldStatusPill extends StatelessWidget {
  const FieldStatusPill({super.key, required this.status});

  final String? status;

  @override
  Widget build(BuildContext context) {
    final (label, color) = switch (status) {
      'scheduled' => ('Scheduled', AppColors.inkMute),
      'finished' => ('Finished', AppColors.inkSub),
      'cut' => ('Cut', AppColors.neg),
      'made_cut_did_not_finish' => ('Made Cut, DNF', AppColors.warn),
      'withdrawn' => ('Withdrawn', AppColors.neg),
      null => ('--', AppColors.inkMute),
      // Unrecognized -- treat as still playing, not silently mislabeled.
      // Title-cased from the raw status name rather than shouted
      // uppercase, matching every recognized label's own casing above.
      _ => (status!.replaceAll('_', ' ').split(' ').map((w) => w.isEmpty ? w : '${w[0].toUpperCase()}${w.substring(1)}').join(' '), AppColors.live),
    };

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(color: color.withValues(alpha: 0.15), borderRadius: BorderRadius.circular(999)),
      child: Text(label, style: AppTextStyles.microLabel(color: color), maxLines: 1, softWrap: false, overflow: TextOverflow.ellipsis),
    );
  }
}
