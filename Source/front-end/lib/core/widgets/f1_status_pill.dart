import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// map_status vocabulary (library/normalize/f1.py) -- F1StatusPill's own
/// switch references these instead of retyping the raw string. A
/// separate class from field_status_pill.dart's PgaParticipantStatus
/// (PGA's own status vocabulary is a genuinely different set of strings
/// -- reusing it here would mis-color a real "classified" or "dnf"
/// result as if the driver were still racing, since neither string is in
/// PGA's own recognized set) -- same "parallel files, don't generalize
/// the shared one" precedent field_prediction_computing_retry.dart's own
/// doc comment already establishes.
abstract final class F1DriverStatus {
  static const finished = 'finished';
  // Finished but didn't cover the real >=90%-distance classification
  // threshold -- still counts (points/finish_position are real).
  static const classified = 'classified';
  static const dnf = 'dnf';
  static const dsq = 'dsq';
  static const dns = 'dns';

  // F1StatusPill's own display label for each value above.
  static const finishedLabel = 'Finished';
  static const classifiedLabel = 'Classified';
  static const dnfLabel = 'DNF';
  static const dsqLabel = 'DSQ';
  static const dnsLabel = 'DNS';
}

/// One driver's own race/qualifying status, as a small colored pill --
/// F1DriverStatus above.
class F1StatusPill extends StatelessWidget {
  const F1StatusPill({super.key, required this.status, this.dotOnly = false});

  final String? status;
  // True on a narrow (compact) viewport -- collapses to just the colored
  // dot, keeping the color-coded signal without text.
  final bool dotOnly;

  @override
  Widget build(BuildContext context) {
    final (label, color) = switch (status) {
      F1DriverStatus.finished => (F1DriverStatus.finishedLabel, AppColors.inkSub),
      // Finished but didn't cover the real >=90%-distance classification
      // threshold -- still counts (points/finish_position are real), so
      // this is NOT a negative color the way dnf/dsq are.
      F1DriverStatus.classified => (F1DriverStatus.classifiedLabel, AppColors.inkSub),
      F1DriverStatus.dnf => (F1DriverStatus.dnfLabel, AppColors.neg),
      F1DriverStatus.dsq => (F1DriverStatus.dsqLabel, AppColors.neg),
      F1DriverStatus.dns => (F1DriverStatus.dnsLabel, AppColors.warn),
      // No real result recorded for this driver yet -- a real, meaningful
      // word rather than a bare dash that reads as missing/broken data
      // (real complaint 2026-08-31: "status is blank instead of a real
      // status"). True for every driver on a not-yet-run event, since
      // there's no per-driver result at all until the race (or, for
      // qualifying-only, that session) actually happens.
      null => ('Scheduled', AppColors.inkMute),
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
