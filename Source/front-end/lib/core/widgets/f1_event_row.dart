import 'package:flutter/material.dart';

import '../models/event_status.dart';
import '../models/f1_event.dart';
import '../models/f1_live_score.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';
import 'event_card.dart';
import 'live_status_pill.dart';
import 'sprint_badge.dart';

/// "Aug 23, 2026" from event_date (YYYY-MM-DD) -- unlike FieldEventRow's
/// own _dateRangeLabel, F1 has no end_date/multi-day-range concept at all
/// (a Grand Prix weekend is one calendar row here, not a course cut
/// window), so this is always a single date.
String _dateLabel(F1Event event) {
  final date = DateTime.tryParse(event.eventDate);
  if (date == null) return '';
  return '${monthAbbreviations[date.month - 1]} ${date.day}, ${date.year}';
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
    return EventCard(
      sport: sport,
      eventId: event.eventId,
      title: Row(
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
      dateLabel: _dateLabel(event),
      isCompleted: isCompleted,
      venueLabel: event.venueLabel,
      // LIVE (the session is actually running right now) takes priority
      // over FINAL/UPCOMING -- a race can't be both, and isLive already
      // implies !isCompleted.
      trailing: isLive ? const LiveStatusPill() : null,
    );
  }
}
