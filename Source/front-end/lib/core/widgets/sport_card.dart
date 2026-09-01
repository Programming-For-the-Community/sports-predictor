import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../data/live_scores_repository.dart';
import '../models/event_status.dart';
import '../models/field_live_score.dart';
import '../models/field_prediction.dart' show PgaEventType;
import '../models/sport_config.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';
import 'field_status_pill.dart' show PgaParticipantStatus;
import 'live_status_pill.dart';

// A PGA field-shape participant/match counts as "actually happening right
// now", not just within its poll window -- see live_scores_repository.
// dart's own docstring: presence in this map alone only means "within the
// poll window" (which for a field event starts as soon as its next tee
// time is due, same pre-start lead-in h2h sports signal via
// LiveEventState.live == false). match/field participant status comes
// from library/normalize/pga.py's own map_status vocabulary (shared by
// golfer and match-play results); a cup event's participants carry no
// status field at all, so its own top-level status (scheduled/completed
// binary) is the only signal available. F1's own live cache carries no
// such per-driver status vocabulary at all (ESPN's own shape -- see
// f1_live_score.dart's docstring), so F1 uses its own, simpler check
// below (F1LiveEventState.isLive) rather than this one.
bool _fieldEntryIsLive(PgaLiveEventState state) => switch (state) {
      PgaFieldLiveState(:final state) => state.participants.values.any((p) => p.status == PgaParticipantStatus.inProgress),
      PgaTwoSidedLiveState(:final state) => state.eventType == PgaEventType.cup
          ? state.status != EventStatus.completed
          : state.participants.values.any((p) => p.status == PgaParticipantStatus.inProgress),
    };

/// design/FRONTEND_STYLE.md's "Sport card" component. Inactive (not yet
/// live on the backend) sport cards are non-interactive with a muted
/// "VIEW-ONLY"/"SOON" treatment. An active sport's own status badge/dot
/// additionally reflects whether it has any event actually live right now
/// (not just "implemented") -- LIVE/glowing dot when it does, a muted
/// ACTIVE/dim dot otherwise.
class SportCard extends ConsumerWidget {
  const SportCard({super.key, required this.sport});

  final SportConfig sport;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final active = sport.active;
    final accentStrip = sport.eventShape == EventShape.headToHead ? AppColors.accentStripH2h : AppColors.accentStripField;

    // Only watched for an active sport -- an inactive one has no live-
    // scores route to call at all. F1 gets its own branch here (not the
    // PGA one below) -- same "own live-scores shape, not PGA's" reasoning
    // f1_live_score.dart's own docstring gives.
    final live = active &&
        (sport.eventShape == EventShape.headToHead
            ? (ref.watch(liveScoresProvider(sport.id)).value?.values.any((s) => s.live) ?? false)
            : sport.id == SportIds.f1
                ? (ref.watch(f1LiveScoresProvider(sport.id)).value?.values.any((s) => s.isLive) ?? false)
                : (ref.watch(pgaLiveScoresProvider(sport.id)).value?.values.any(_fieldEntryIsLive) ?? false));

    return Opacity(
      opacity: active ? 1 : 0.55,
      child: InkWell(
        onTap: active ? () => context.go('/${sport.id}/events') : null,
        borderRadius: BorderRadius.circular(18),
        child: Container(
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(18),
            gradient: LinearGradient(
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
              colors: AppColors.surfaceGrad,
            ),
            border: Border.all(color: AppColors.border),
          ),
          clipBehavior: Clip.antiAlias,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(height: 4, decoration: BoxDecoration(gradient: accentStrip)),
              Padding(
                padding: const EdgeInsets.all(26),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        // Glowing at full accent color while live, dim
                        // (no glow) otherwise -- muted gray entirely when
                        // not yet implemented.
                        Container(
                          width: 8,
                          height: 8,
                          decoration: BoxDecoration(
                            shape: BoxShape.circle,
                            color: !active ? AppColors.inkMute : sport.accentColor.withValues(alpha: live ? 1 : 0.35),
                            boxShadow: live ? [BoxShadow(color: sport.accentColor, blurRadius: 10)] : null,
                          ),
                        ),
                        const SizedBox(width: 10),
                        // Expanded so the display name gives ground and
                        // ellipsizes on a narrow (mobile) card instead of
                        // overflowing past the status pill.
                        Expanded(
                          child: Text(
                            sport.displayName,
                            style: AppTextStyles.cardTitle(),
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                        const SizedBox(width: 8),
                        _StatusPill(active: active, live: live),
                      ],
                    ),
                    const SizedBox(height: 6),
                    Text(
                      sport.eventShape == EventShape.headToHead ? 'HEAD-TO-HEAD' : 'FIELD EVENT',
                      style: AppTextStyles.microLabel(),
                    ),
                    const SizedBox(height: 18),
                    Container(
                      padding: const EdgeInsets.all(14),
                      decoration: BoxDecoration(color: AppColors.inset, borderRadius: BorderRadius.circular(12)),
                      child: Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Flexible(
                            child: Text(
                              active ? 'View predictions' : 'Coming soon',
                              style: AppTextStyles.body(color: AppColors.inkSub),
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                          if (active)
                            Icon(Icons.arrow_forward, size: 16, color: sport.accentColor)
                          else
                            Text('VIEW-ONLY', style: AppTextStyles.microLabel()),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// Three states: not implemented (SOON, muted), implemented with something
// live right now (LIVE, green -- LiveStatusPill.label, not retyped here),
// implemented with nothing live right now (ACTIVE, muted -- distinct
// wording from SOON so "not yet built" and "built, just quiet right now"
// don't read as the same thing). Not shared with any other file, but
// named instead of typed inline.
abstract final class _SportCardStatusLabels {
  static const soon = 'SOON';
  static const active = 'ACTIVE';
}

class _StatusPill extends StatelessWidget {
  const _StatusPill({required this.active, required this.live});
  final bool active;
  final bool live;

  @override
  Widget build(BuildContext context) {
    final label = !active ? _SportCardStatusLabels.soon : (live ? LiveStatusPill.label : _SportCardStatusLabels.active);
    final color = live ? AppColors.live : AppColors.inkMute;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: live ? AppColors.live.withValues(alpha: 0.15) : AppColors.inset,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(label, style: AppTextStyles.microLabel(color: color)),
    );
  }
}
