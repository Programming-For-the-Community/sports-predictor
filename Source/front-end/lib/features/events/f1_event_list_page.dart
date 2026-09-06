import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/data/f1_events_repository.dart';
import '../../core/data/live_scores_repository.dart';
import '../../core/models/event_status.dart';
import '../../core/models/f1_live_score.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/f1_event_row.dart';
import '../../core/widgets/status_toggle.dart';

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
  String _status = EventStatus.scheduled;
  Timer? _liveScoresTimer;

  @override
  void initState() {
    super.initState();
    _scheduleLiveScoresPoll();
  }

  @override
  void dispose() {
    _liveScoresTimer?.cancel();
    super.dispose();
  }

  // Only while showing Upcoming -- same split event_list_page.dart's own
  // _scheduleLiveScoresPoll uses. Torn down and rebuilt on every status
  // change so switching to Completed actually stops the ticking.
  void _scheduleLiveScoresPoll() {
    _liveScoresTimer?.cancel();
    if (_status != EventStatus.scheduled) return;
    _liveScoresTimer = Timer.periodic(const Duration(seconds: 30), (_) {
      ref.invalidate(f1LiveScoresProvider(widget.sportId));
    });
  }

  void _setStatus(String status) {
    setState(() => _status = status);
    _scheduleLiveScoresPoll();
  }

  @override
  Widget build(BuildContext context) {
    final events = ref.watch(f1EventsListProvider((sport: widget.sportId, status: _status)));
    // Only fetched/watched for the Upcoming tab -- same split
    // event_list_page.dart's own liveScores local uses. A race whose real
    // result our own storage hasn't caught up with yet (ingest lag -- see
    // live_scores.py's own refresh() docstring) is still status
    // "scheduled" here, which is exactly why F1EventRow needs this to
    // show FINAL/LIVE instead of a stale "UPCOMING".
    final liveScores = _status == EventStatus.scheduled
        ? ref.watch(f1LiveScoresProvider(widget.sportId)).value ?? const <String, F1LiveEventState>{}
        : const <String, F1LiveEventState>{};

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
                  final message = _status == EventStatus.scheduled ? 'Coming Soon' : 'No races found.';
                  return Text(message, style: AppTextStyles.body(color: AppColors.inkSub));
                }
                // Soonest-first for Upcoming, most-recent-first for
                // Completed -- both read top-to-bottom as "closest to now
                // at the top" (same convention field_event_list_page.dart uses).
                final sorted = [...list]..sort(
                    (a, b) => _status == EventStatus.scheduled
                        ? a.eventDate.compareTo(b.eventDate)
                        : b.eventDate.compareTo(a.eventDate),
                  );
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    for (final event in sorted) ...[
                      F1EventRow(sport: widget.sportId, event: event, liveState: liveScores[event.eventId]),
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
