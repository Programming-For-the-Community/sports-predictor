import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../models/event_status.dart';
import '../models/f1_event.dart';
import '../models/f1_live_score.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';
import 'live_status_pill.dart';
import 'sprint_badge.dart';

const _months = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

/// "Aug 23, 2026" from event_date (YYYY-MM-DD) -- unlike FieldEventRow's
/// own _dateRangeLabel, F1 has no end_date/multi-day-range concept at all
/// (a Grand Prix weekend is one calendar row here, not a course cut
/// window), so this is always a single date.
String _dateLabel(F1Event event) {
  final date = DateTime.tryParse(event.eventDate);
  if (date == null) return '';
  return '${_months[date.month - 1]} ${date.day}, ${date.year}';
}

/// Used for both F1 event_type values (field/sprint) -- GET /f1/events
/// returns homogeneous top-level metadata for both, distinguished here
/// only by a small SPRINT badge; the full driver/constructor breakdown
/// only appears on the detail page (f1_event_detail_page.dart).
class F1EventRow extends StatelessWidget {
  const F1EventRow({super.key, required this.sport, required this.event, this.liveState});

  final String sport;
  final F1Event event;
  // From f1LiveScoresProvider -- null for the vast majority of rows (any
  // race not currently live or recently finished). event.status alone
  // can lag the real result by up to ~24h (ingest only re-fetches Jolpica
  // once a day -- see live_scores.py's own refresh() docstring), so
  // liveState.isFinished is checked alongside isCompleted below rather
  // than trusting event.status on its own; without it, a race that had
  // already run showed the same "UPCOMING" pill as one that hadn't.
  final F1LiveEventState? liveState;

  @override
  Widget build(BuildContext context) {
    final isCompleted = event.status == EventStatus.completed || (liveState?.isFinished ?? false);
    final isLive = liveState?.isLive ?? false;
    return InkWell(
      onTap: () => context.go('/$sport/events/${event.eventId}'),
      borderRadius: BorderRadius.circular(16),
      child: Container(
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: AppColors.border),
        ),
        clipBehavior: Clip.antiAlias,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(height: 4, decoration: const BoxDecoration(gradient: AppColors.accentStripField)),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
              child: Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Flexible(
                              child: Text(
                                event.raceName ?? 'Grand Prix',
                                style: AppTextStyles.body(color: AppColors.ink),
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                              ),
                            ),
                            if (event.isSprint) ...[
                              const SizedBox(width: 8),
                              const SprintBadge(),
                            ],
                          ],
                        ),
                        const SizedBox(height: 4),
                        Text(_dateLabel(event), style: AppTextStyles.microLabel(color: AppColors.inkMute)),
                        if (event.venueLabel != null) ...[
                          const SizedBox(height: 4),
                          Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Icon(Icons.location_on_outlined, size: 12, color: AppColors.inkMute),
                              const SizedBox(width: 4),
                              Flexible(
                                child: Text(
                                  event.venueLabel!,
                                  style: AppTextStyles.microLabel(color: AppColors.inkMute),
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                ),
                              ),
                            ],
                          ),
                        ],
                      ],
                    ),
                  ),
                  const SizedBox(width: 12),
                  // LIVE (the session is actually running right now)
                  // takes priority over FINAL/UPCOMING -- a race can't be
                  // both, and isLive already implies !isCompleted.
                  if (isLive)
                    const LiveStatusPill()
                  else
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                      decoration: BoxDecoration(
                        color: (isCompleted ? AppColors.inkMute : AppColors.violet).withValues(alpha: 0.15),
                        borderRadius: BorderRadius.circular(999),
                      ),
                      child: Text(
                        isCompleted ? 'FINAL' : 'UPCOMING',
                        style: AppTextStyles.microLabel(color: isCompleted ? AppColors.inkMute : AppColors.violet),
                      ),
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
