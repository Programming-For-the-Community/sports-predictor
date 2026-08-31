import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/data/f1_events_repository.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/f1_event_row.dart';

/// F1's own list page -- mirrors field_event_list_page.dart's shell
/// (RefreshIndicator + Upcoming/Completed toggle, flat not grouped: races
/// are weekly, not daily, so a per-day heading bucket would almost always
/// hold exactly one row). Also, unlike GameRow, no per-row prediction
/// fetch -- a field response (~20 drivers + ~10 constructors) is heavier
/// than a head-to-head one, and a season list can hold ~24 race rows.
class F1EventListPage extends ConsumerStatefulWidget {
  const F1EventListPage({super.key, required this.sportId});

  final String sportId;

  @override
  ConsumerState<F1EventListPage> createState() => _F1EventListPageState();
}

class _F1EventListPageState extends ConsumerState<F1EventListPage> {
  String _status = 'scheduled';

  void _setStatus(String status) => setState(() => _status = status);

  @override
  Widget build(BuildContext context) {
    final events = ref.watch(f1EventsListProvider((sport: widget.sportId, status: _status)));

    return RefreshIndicator(
      onRefresh: () => ref.refresh(f1EventsListProvider((sport: widget.sportId, status: _status)).future),
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                _StatusToggle(label: 'Upcoming/Current', selected: _status == 'scheduled', onTap: () => _setStatus('scheduled')),
                _StatusToggle(label: 'Completed', selected: _status == 'completed', onTap: () => _setStatus('completed')),
              ],
            ),
            const SizedBox(height: 16),
            events.when(
              data: (list) {
                if (list.isEmpty) {
                  final message = _status == 'scheduled' ? 'Coming Soon' : 'No races found.';
                  return Text(message, style: AppTextStyles.body(color: AppColors.inkSub));
                }
                // Soonest-first for Upcoming, most-recent-first for
                // Completed -- both read top-to-bottom as "closest to now
                // at the top" (same convention field_event_list_page.dart uses).
                final sorted = [...list]..sort(
                    (a, b) => _status == 'scheduled'
                        ? a.eventDate.compareTo(b.eventDate)
                        : b.eventDate.compareTo(a.eventDate),
                  );
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    for (final event in sorted) ...[
                      F1EventRow(sport: widget.sportId, event: event),
                      const SizedBox(height: 12),
                    ],
                  ],
                );
              },
              loading: () => const Center(child: Padding(padding: EdgeInsets.all(40), child: CircularProgressIndicator())),
              error: (error, _) => Text('Couldn\'t load races: $error', style: AppTextStyles.body(color: AppColors.neg)),
            ),
          ],
        ),
      ),
    );
  }
}

class _StatusToggle extends StatelessWidget {
  const _StatusToggle({required this.label, required this.selected, required this.onTap});
  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(999),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        decoration: BoxDecoration(
          color: selected ? AppColors.surface : null,
          border: Border.all(color: selected ? AppColors.violet : AppColors.border),
          borderRadius: BorderRadius.circular(999),
        ),
        child: Text(label, style: AppTextStyles.microLabel(color: selected ? AppColors.violet : AppColors.inkMute)),
      ),
    );
  }
}
