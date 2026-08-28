import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// One golfer's own round/tournament status, as a small colored pill --
/// map_status vocabulary (library/normalize/pga.py): scheduled/finished/
/// cut/made_cut_did_not_finish/withdrawn/in_progress, plus any other
/// unrecognized value, which falls through to the "still playing" branch
/// below rather than being silently mislabeled.
class FieldStatusPill extends StatelessWidget {
  const FieldStatusPill({super.key, required this.status, this.dotOnly = false});

  final String? status;
  // True on a narrow (compact) viewport -- field_leaderboard_table.dart's
  // STATUS column has no room for a full text label there (confirmed
  // live: a real "Made Cut, DNF" pill was one of the widest single
  // strings in the whole table, and STATUS shares its column width with
  // #/PLAYER/TOTAL on mobile). Collapses to just the colored dot,
  // keeping the color-coded signal without the text.
  final bool dotOnly;

  @override
  Widget build(BuildContext context) {
    final (label, color) = switch (status) {
      'scheduled' => ('Scheduled', AppColors.inkMute),
      'finished' => ('Finished', AppColors.inkSub),
      'cut' => ('Cut', AppColors.neg),
      'made_cut_did_not_finish' => ('Made Cut, DNF', AppColors.warn),
      'withdrawn' => ('Withdrawn', AppColors.neg),
      'in_progress' => ('In Progress', AppColors.live),
      null => ('--', AppColors.inkMute),
      // Unrecognized -- treat as still playing, not silently mislabeled.
      // Title-cased from the raw status name rather than shouted
      // uppercase, matching every recognized label's own casing above.
      _ => (status!.replaceAll('_', ' ').split(' ').map((w) => w.isEmpty ? w : '${w[0].toUpperCase()}${w.substring(1)}').join(' '), AppColors.live),
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
