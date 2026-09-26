import 'package:flutter/material.dart';

import '../models/event_status.dart';
import '../models/field_event.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';
import 'event_card.dart';

/// "Aug 20-23, 2026" from event_date/end_date (both YYYY-MM-DD) --
/// degrades to a single date if end_date is missing/unparseable or equal
/// to event_date (a single-day/no-cut event).
String _dateRangeLabel(FieldEvent event) {
  final start = DateTime.tryParse(event.eventDate);
  if (start == null) return '';
  final end = event.endDate != null ? DateTime.tryParse(event.endDate!) : null;
  final startLabel = '${monthAbbreviations[start.month - 1]} ${start.day}';
  if (end == null || end.difference(start).inDays <= 0) {
    return '$startLabel, ${start.year}';
  }
  // Same month -- "Aug 20-23, 2026"; different month -- "Aug 30-Sep 2, 2026".
  final endLabel = end.month == start.month ? '${end.day}' : '${monthAbbreviations[end.month - 1]} ${end.day}';
  return '$startLabel-$endLabel, ${end.year}';
}

/// Used uniformly for every row GET /pga/events returns (field/cup, never
/// a cup's own match rows) -- homogeneous top-level metadata across
/// event_types, and the list page shows no per-row prediction (a full field
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
    return EventCard(
      sport: sport,
      eventId: event.eventId,
      title: Text(
        event.tournamentName ?? 'Tournament',
        style: AppTextStyles.body(color: AppColors.ink),
      ),
      dateLabel: _dateRangeLabel(event),
      isCompleted: event.status == EventStatus.completed,
      venueLabel: event.venueLabel,
    );
  }
}
