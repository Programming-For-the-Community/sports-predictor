import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../routing/app_routes.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

const monthAbbreviations = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

/// Shared event-list-row card shell -- FieldEventRow/F1EventRow both use
/// this exact shape (gradient strip, title/date/venue column, a trailing
/// status widget), differing only in the title widget, the date label
/// string, and what the trailing status widget is (a plain FINAL/
/// UPCOMING pill by default, or `trailing` to show something else
/// instead -- F1's own LIVE state, for example).
class EventCard extends StatelessWidget {
  const EventCard({
    super.key,
    required this.sport,
    required this.eventId,
    required this.title,
    required this.dateLabel,
    required this.isCompleted,
    this.venueLabel,
    this.trailing,
  });

  final String sport;
  final String eventId;
  final Widget title;
  final String dateLabel;
  final bool isCompleted;
  final String? venueLabel;
  // Null shows the default FINAL/UPCOMING pill (isCompleted-driven).
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: () => context.go(AppRoutes.eventDetail(sport, eventId)),
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
                        title,
                        const SizedBox(height: 4),
                        Text(dateLabel, style: AppTextStyles.microLabel(color: AppColors.inkMute)),
                        if (venueLabel != null) ...[
                          const SizedBox(height: 4),
                          Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              const Icon(Icons.location_on_outlined, size: 12, color: AppColors.inkMute),
                              const SizedBox(width: 4),
                              Flexible(
                                child: Text(
                                  venueLabel!,
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
                  trailing ?? _StatusPill(isCompleted: isCompleted),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _StatusPill extends StatelessWidget {
  const _StatusPill({required this.isCompleted});
  final bool isCompleted;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: (isCompleted ? AppColors.inkMute : AppColors.violet).withValues(alpha: 0.15),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        isCompleted ? 'FINAL' : 'UPCOMING',
        style: AppTextStyles.microLabel(color: isCompleted ? AppColors.inkMute : AppColors.violet),
      ),
    );
  }
}
