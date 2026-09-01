import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../models/event_status.dart';
import '../models/field_event.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

const _months = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

/// "Aug 20-23, 2026" from event_date/end_date (both YYYY-MM-DD) --
/// degrades to a single date if end_date is missing/unparseable or equal
/// to event_date (a single-day/no-cut event).
String _dateRangeLabel(FieldEvent event) {
  final start = DateTime.tryParse(event.eventDate);
  if (start == null) return '';
  final end = event.endDate != null ? DateTime.tryParse(event.endDate!) : null;
  final startLabel = '${_months[start.month - 1]} ${start.day}';
  if (end == null || end.difference(start).inDays <= 0) {
    return '$startLabel, ${start.year}';
  }
  // Same month -- "Aug 20-23, 2026"; different month -- "Aug 30-Sep 2, 2026".
  final endLabel = end.month == start.month ? '${end.day}' : '${_months[end.month - 1]} ${end.day}';
  return '$startLabel-$endLabel, ${end.year}';
}

/// Used uniformly for all three PGA event_type values (field/match_play/
/// cup) -- GET /pga/events returns homogeneous top-level metadata across
/// all three, and the list page shows no per-row prediction (a full field
/// response is too heavy to fetch per row in a ~45-tournament season
/// list), so there's no meaningful list-level distinction to branch on --
/// see field_event_detail_page.dart for where the event_type branch
/// actually happens.
class FieldEventRow extends StatelessWidget {
  const FieldEventRow({super.key, required this.sport, required this.event});

  final String sport;
  final FieldEvent event;

  @override
  Widget build(BuildContext context) {
    final isCompleted = event.status == EventStatus.completed;
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
                        Text(
                          event.tournamentName ?? 'Tournament',
                          style: AppTextStyles.body(color: AppColors.ink),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                        const SizedBox(height: 4),
                        Text(_dateRangeLabel(event), style: AppTextStyles.microLabel(color: AppColors.inkMute)),
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
