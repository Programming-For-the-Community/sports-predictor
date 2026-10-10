import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/data/field_events_repository.dart';
import '../../core/models/event_status.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/field_event_row.dart';
import '../../core/widgets/status_toggle.dart';

/// PGA's own list page -- mirrors event_list_page.dart's shell
/// (RefreshIndicator + Upcoming/Completed toggle) but flat, not grouped:
/// no conference grouping (golfers have no team/conference concept) and
/// no per-day grouping either (tournaments are weekly, not daily, so a
/// day-heading bucket would almost always hold exactly one row). Only the
/// next tournament fetches its prediction -- a field response (~150
/// golfers) is too heavy to fetch for each of a season's ~45 rows.
class FieldEventListPage extends ConsumerStatefulWidget {
  const FieldEventListPage({super.key, required this.sportId});

  final String sportId;

  @override
  ConsumerState<FieldEventListPage> createState() => _FieldEventListPageState();
}

class _FieldEventListPageState extends ConsumerState<FieldEventListPage> {
  String _status = EventStatus.scheduled;

  void _setStatus(String status) => setState(() => _status = status);

  @override
  Widget build(BuildContext context) {
    final events = ref.watch(fieldEventsListProvider((sport: widget.sportId, status: _status)));

    return RefreshIndicator(
      onRefresh: () => ref.refresh(fieldEventsListProvider((sport: widget.sportId, status: _status)).future),
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Wrap, not Row -- same overflow-avoidance reasoning as
            // event_list_page.dart's own toggle row.
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                StatusToggle(
                  label: StatusToggleLabels.upcoming,
                  selected: _status == EventStatus.scheduled,
                  onTap: () => _setStatus(EventStatus.scheduled),
                  accentColor: AppColors.violet,
                ),
                StatusToggle(
                  label: StatusToggleLabels.completed,
                  selected: _status == EventStatus.completed,
                  onTap: () => _setStatus(EventStatus.completed),
                  accentColor: AppColors.violet,
                ),
              ],
            ),
            const SizedBox(height: 16),
            events.when(
              data: (list) {
                if (list.isEmpty) {
                  final message = _status == EventStatus.scheduled ? 'Coming Soon' : 'No tournaments found.';
                  return Text(message, style: AppTextStyles.body(color: AppColors.inkSub));
                }
                // Soonest-first for Upcoming, most-recent-first for
                // Completed -- both read top-to-bottom as "closest to now
                // at the top" (same convention event_list_page.dart uses).
                final sorted = [...list]..sort(
                    (a, b) => _status == EventStatus.scheduled
                        ? a.eventDate.compareTo(b.eventDate)
                        : b.eventDate.compareTo(a.eventDate),
                  );
                // The next tournament leads the Upcoming list with the model's picks.
                final isUpcoming = _status == EventStatus.scheduled;
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    for (final (index, event) in sorted.indexed) ...[
                      if (isUpcoming && index == 0)
                        FeaturedFieldEvent(sport: widget.sportId, event: event)
                      else
                        FieldEventRow(sport: widget.sportId, event: event),
                      const SizedBox(height: 12),
                    ],
                  ],
                );
              },
              loading: () => const Center(child: Padding(padding: EdgeInsets.all(40), child: CircularProgressIndicator())),
              error: (error, _) => Text('Couldn\'t load tournaments: $error', style: AppTextStyles.body(color: AppColors.neg)),
            ),
          ],
        ),
      ),
    );
  }
}
