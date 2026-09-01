import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// map_status vocabulary (library/normalize/pga.py) -- FieldStatusPill's
/// own switch, plus every other file that tests one of these values
/// (sport_card.dart, field_leaderboard_table.dart), reference these
/// instead of retyping the raw string.
abstract final class PgaParticipantStatus {
  static const scheduled = 'scheduled';
  static const finished = 'finished';
  static const cut = 'cut';
  static const madeCutDidNotFinish = 'made_cut_did_not_finish';
  static const withdrawn = 'withdrawn';
  static const inProgress = 'in_progress';

  // FieldStatusPill's own display label for each value above -- not
  // shared with any other file, but named instead of typed inline in the
  // pill's switch.
  static const scheduledLabel = 'Scheduled';
  static const finishedLabel = 'Finished';
  static const cutLabel = 'Cut';
  static const madeCutDidNotFinishLabel = 'Made Cut, DNF';
  static const withdrawnLabel = 'Withdrawn';
  static const inProgressLabel = 'In Progress';
}

/// One golfer's own round/tournament status, as a small colored pill --
/// PgaParticipantStatus above, plus any other unrecognized value, which
/// falls through to the "still playing" branch below.
class FieldStatusPill extends StatelessWidget {
  const FieldStatusPill({super.key, required this.status, this.dotOnly = false});

  final String? status;
  // True on a narrow (compact) viewport -- field_leaderboard_table.dart's
  // STATUS column has no room for a full text label there. Collapses to
  // just the colored dot, keeping the color-coded signal without text.
  final bool dotOnly;

  @override
  Widget build(BuildContext context) {
    final (label, color) = switch (status) {
      PgaParticipantStatus.scheduled => (PgaParticipantStatus.scheduledLabel, AppColors.inkMute),
      PgaParticipantStatus.finished => (PgaParticipantStatus.finishedLabel, AppColors.inkSub),
      PgaParticipantStatus.cut => (PgaParticipantStatus.cutLabel, AppColors.neg),
      PgaParticipantStatus.madeCutDidNotFinish => (PgaParticipantStatus.madeCutDidNotFinishLabel, AppColors.warn),
      PgaParticipantStatus.withdrawn => (PgaParticipantStatus.withdrawnLabel, AppColors.neg),
      PgaParticipantStatus.inProgress => (PgaParticipantStatus.inProgressLabel, AppColors.live),
      null => ('--', AppColors.inkMute),
      // Unrecognized -- treat as still playing. Title-cased from the raw
      // status name, matching every recognized label's own casing above.
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
