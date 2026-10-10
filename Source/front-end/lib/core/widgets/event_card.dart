import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../routing/app_routes.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

const monthAbbreviations = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

/// The day (or day range) over the month, for a row's date block: ("22",
/// "OCT"), ("8–11", "OCT"), or ("30–2", "OCT") for a range crossing a month.
(String days, String month) dateBlockLabels(DateTime start, DateTime? end) {
  final month = monthAbbreviations[start.month - 1].toUpperCase();
  if (end == null || !end.isAfter(start)) return ('${start.day}', month);
  return ('${start.day}–${end.day}', month);
}

/// "TODAY", "TOMORROW" or "IN 12 DAYS" for an event starting on [start];
/// null once it has started.
String? startsInLabel(DateTime start, DateTime now) {
  final days = DateTime(start.year, start.month, start.day).difference(DateTime(now.year, now.month, now.day)).inDays;
  if (days < 0) return null;
  if (days == 0) return 'TODAY';
  return days == 1 ? 'TOMORROW' : 'IN $days DAYS';
}

/// One named session of an event (an F1 weekend's sprint and Grand Prix),
/// opening its own page.
class EventSession {
  const EventSession({required this.eventId, required this.label});

  final String eventId;
  final String label;
}

/// One chip per session, each opening that session's page; the one for
/// [selectedEventId] is highlighted.
class EventSessionChips extends StatelessWidget {
  const EventSessionChips({super.key, required this.sport, required this.sessions, this.selectedEventId});

  final String sport;
  final List<EventSession> sessions;
  final String? selectedEventId;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        for (final session in sessions)
          InkWell(
            onTap: () => context.go(AppRoutes.eventDetail(sport, session.eventId)),
            borderRadius: BorderRadius.circular(999),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
              decoration: BoxDecoration(
                color: session.eventId == selectedEventId ? AppColors.surface : null,
                border: Border.all(color: session.eventId == selectedEventId ? AppColors.violet : AppColors.border),
                borderRadius: BorderRadius.circular(999),
              ),
              child: Text(
                session.label,
                style: AppTextStyles.microLabel(color: session.eventId == selectedEventId ? AppColors.violet : AppColors.inkSub),
              ),
            ),
          ),
      ],
    );
  }
}

Widget _venueLine(String venue) => Row(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Padding(
          padding: EdgeInsets.only(top: 1),
          child: Icon(Icons.location_on_outlined, size: 12, color: AppColors.inkMute),
        ),
        const SizedBox(width: 4),
        Flexible(child: Text(venue, style: AppTextStyles.microLabel(color: AppColors.inkMute))),
      ],
    );

/// A PGA tournament or F1 weekend in the events list: a date block, the name
/// and venue, any [sessions] as chips, and [trailing] (when it starts, the
/// year, or a LIVE pill).
class EventCard extends StatelessWidget {
  const EventCard({
    super.key,
    required this.sport,
    required this.eventId,
    required this.title,
    required this.dateBlock,
    this.venueLabel,
    this.trailing,
    this.sessions = const [],
  });

  final String sport;
  // The page a tap on the card opens.
  final String eventId;
  final Widget title;
  final (String days, String month)? dateBlock;
  final String? venueLabel;
  final Widget? trailing;
  final List<EventSession> sessions;

  @override
  Widget build(BuildContext context) {
    final block = dateBlock;
    final trailing = this.trailing;
    return InkWell(
      onTap: () => context.go(AppRoutes.eventDetail(sport, eventId)),
      borderRadius: BorderRadius.circular(16),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: AppColors.border),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (block != null) ...[
              SizedBox(
                width: 56,
                child: Column(
                  children: [
                    FittedBox(fit: BoxFit.scaleDown, child: Text(block.$1, style: AppTextStyles.metricValue())),
                    Text(block.$2, style: AppTextStyles.microLabel(color: AppColors.inkSub)),
                  ],
                ),
              ),
              const SizedBox(width: 14),
            ],
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  title,
                  if (venueLabel != null) ...[const SizedBox(height: 4), _venueLine(venueLabel!)],
                  if (sessions.isNotEmpty) ...[const SizedBox(height: 10), EventSessionChips(sport: sport, sessions: sessions)],
                ],
              ),
            ),
            if (trailing != null) ...[const SizedBox(width: 12), trailing],
          ],
        ),
      ),
    );
  }
}

/// One of the model's picks on a featured card.
class FeaturedPick {
  const FeaturedPick({required this.name, required this.value, this.detail});

  final String name;
  // A driver's team, for example.
  final String? detail;
  // "34%".
  final String value;
}

// Below this width the picks drop under the event's own details.
const _featuredStackBreakpoint = 600.0;

/// The next (or in-progress) event, opening the Upcoming list with the
/// model's top three for it. [picks] is null for an event with no field
/// prediction to rank; while it loads or fails the card shows without them.
class FeaturedEventCard extends StatelessWidget {
  const FeaturedEventCard({
    super.key,
    required this.sport,
    required this.eventId,
    required this.eyebrow,
    required this.title,
    required this.picksLabel,
    required this.picks,
    this.eyebrowColor = AppColors.violet,
    this.subtitle,
    this.venueLabel,
    this.sessions = const [],
  });

  final String sport;
  final String eventId;
  final String eyebrow;
  final Color eyebrowColor;
  final String title;
  // The dates, when the eyebrow doesn't already carry the timing.
  final String? subtitle;
  final String? venueLabel;
  final List<EventSession> sessions;
  final String picksLabel;
  final AsyncValue<List<FeaturedPick>>? picks;

  Widget _details() => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(eyebrow, style: AppTextStyles.microLabel(color: eyebrowColor)),
          const SizedBox(height: 4),
          Text(title, style: AppTextStyles.sectionTitle().copyWith(fontSize: 20, fontWeight: FontWeight.w700)),
          if (subtitle != null) ...[
            const SizedBox(height: 4),
            Text(subtitle!, style: AppTextStyles.body(color: AppColors.inkSub)),
          ],
          if (venueLabel != null) ...[const SizedBox(height: 6), _venueLine(venueLabel!)],
          if (sessions.isNotEmpty) ...[
            const SizedBox(height: 12),
            EventSessionChips(sport: sport, sessions: sessions, selectedEventId: eventId),
          ],
        ],
      );

  Widget _picks(List<FeaturedPick> picks) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(picksLabel, style: AppTextStyles.microLabel()),
          for (final (index, pick) in picks.indexed)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SizedBox(width: 22, child: Text('${index + 1}', style: AppTextStyles.metricValue(color: AppColors.inkMute))),
                  Expanded(
                    child: Text.rich(
                      TextSpan(
                        text: pick.name,
                        style: AppTextStyles.body(color: AppColors.ink),
                        children: [
                          if (pick.detail != null)
                            TextSpan(text: ' · ${pick.detail}', style: AppTextStyles.body(color: AppColors.inkSub)),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Text(pick.value, style: AppTextStyles.metricValue(color: AppColors.violet)),
                ],
              ),
            ),
        ],
      );

  @override
  Widget build(BuildContext context) {
    final loaded = picks?.value;
    final picksColumn = loaded == null || loaded.isEmpty ? null : _picks(loaded);
    return InkWell(
      onTap: () => context.go(AppRoutes.eventDetail(sport, eventId)),
      borderRadius: BorderRadius.circular(16),
      child: Container(
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: AppColors.borderRaised),
        ),
        clipBehavior: Clip.antiAlias,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Container(height: 3, decoration: const BoxDecoration(gradient: AppColors.accentStripField)),
            Padding(
              padding: const EdgeInsets.all(18),
              child: LayoutBuilder(
                builder: (context, constraints) {
                  if (picksColumn == null) return _details();
                  if (constraints.maxWidth < _featuredStackBreakpoint) {
                    return Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [_details(), const SizedBox(height: 16), picksColumn],
                    );
                  }
                  return Row(
                    crossAxisAlignment: CrossAxisAlignment.center,
                    children: [Expanded(child: _details()), const SizedBox(width: 24), Expanded(child: picksColumn)],
                  );
                },
              ),
            ),
          ],
        ),
      ),
    );
  }
}
