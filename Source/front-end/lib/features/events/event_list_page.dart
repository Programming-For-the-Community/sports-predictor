import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/data/events_repository.dart';
import '../../core/data/live_scores_repository.dart';
import '../../core/models/event.dart';
import '../../core/models/live_score.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/game_row.dart';

const _weekdayNames = [
  'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday',
];
const _monthNames = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

/// kickoffTime sorts/groups correctly down to the minute; eventDate (used
/// only when an older event has no kickoffTime) is day-only, so games on
/// the same day but different times can't be told apart by it.
String _sortKey(SportEvent event) => event.kickoffTime ?? event.eventDate;

/// "Thursday, Sep 11" from an ISO date/timestamp -- falls back to the raw
/// string for anything that doesn't parse.
String _dateHeading(String isoDateOrTimestamp) {
  final date = DateTime.tryParse(isoDateOrTimestamp);
  if (date == null) return isoDateOrTimestamp;
  return '${_weekdayNames[date.weekday - 1]}, ${_monthNames[date.month - 1]} ${date.day}';
}

/// Groups already-sorted events into (heading, events) buckets, one per
/// distinct calendar day.
List<(String, List<SportEvent>)> _groupByDate(List<SportEvent> events) {
  final groups = <(String, List<SportEvent>)>[];
  for (final event in events) {
    final heading = _dateHeading(_sortKey(event));
    if (groups.isNotEmpty && groups.last.$1 == heading) {
      groups.last.$2.add(event);
    } else {
      groups.add((heading, [event]));
    }
  }
  return groups;
}

class EventListPage extends ConsumerStatefulWidget {
  const EventListPage({super.key, required this.sportId});

  final String sportId;

  @override
  ConsumerState<EventListPage> createState() => _EventListPageState();
}

class _EventListPageState extends ConsumerState<EventListPage> {
  String _status = 'scheduled';
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

  // Only while showing Upcoming -- a live game is never relevant on the
  // Completed tab. Torn down and rebuilt on every status change (see
  // _setStatus) so switching to Completed actually stops the ticking
  // rather than just hiding its effect.
  void _scheduleLiveScoresPoll() {
    _liveScoresTimer?.cancel();
    if (_status != 'scheduled') return;
    _liveScoresTimer = Timer.periodic(const Duration(seconds: 30), (_) {
      ref.invalidate(liveScoresProvider(widget.sportId));
    });
  }

  void _setStatus(String status) {
    setState(() => _status = status);
    _scheduleLiveScoresPoll();
  }

  @override
  Widget build(BuildContext context) {
    final events = ref.watch(eventsListProvider((sport: widget.sportId, status: _status)));
    // Only fetched/watched for the Upcoming tab -- see _scheduleLiveScoresPoll.
    final liveScores = _status == 'scheduled'
        ? ref.watch(liveScoresProvider(widget.sportId)).value ?? const <String, LiveEventState>{}
        : const <String, LiveEventState>{};

    return SingleChildScrollView(
      padding: const EdgeInsets.all(24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              _StatusToggle(
                label: 'Upcoming',
                selected: _status == 'scheduled',
                onTap: () => _setStatus('scheduled'),
              ),
              const SizedBox(width: 8),
              _StatusToggle(
                label: 'Completed',
                selected: _status == 'completed',
                onTap: () => _setStatus('completed'),
              ),
            ],
          ),
          const SizedBox(height: 8),
          // Stated once for the whole list rather than repeated on every
          // GameRow -- every kickoff time below is already in this same
          // local time (game_row.dart's own _kickoffTimeLabel), so
          // repeating it per row would just be noise, and there's no
          // spare width in that row's tight time column for it anyway.
          Text('Times shown in your local time (${localTimezoneLabel()})', style: AppTextStyles.microLabel()),
          const SizedBox(height: 12),
          events.when(
            data: (list) {
              if (list.isEmpty) {
                // Both routes are scoped server-side to exactly one week
                // (see handler.py's _next_week_events/_previous_week_events)
                // -- an empty "scheduled" list specifically means next
                // week hasn't been ingested yet (see
                // Terraform/scheduler-nfl-ingest.tf), not that there's
                // nothing to show.
                final message = _status == 'scheduled' ? 'Coming Soon' : 'No games found.';
                return Text(message, style: AppTextStyles.body(color: AppColors.inkSub));
              }
              // Soonest-first for Upcoming, most-recent-first for
              // Completed -- both read top-to-bottom as "closest to now
              // at the top" for their own tab.
              final sorted = [...list]..sort(
                  (a, b) => _status == 'scheduled'
                      ? _sortKey(a).compareTo(_sortKey(b))
                      : _sortKey(b).compareTo(_sortKey(a)),
                );
              return Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  for (final (heading, dayEvents) in _groupByDate(sorted)) ...[
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: Text(heading.toUpperCase(), style: AppTextStyles.microLabel(color: AppColors.inkSub)),
                    ),
                    for (final event in dayEvents) ...[
                      GameRow(sport: widget.sportId, event: event, liveState: liveScores[event.eventId]),
                      const SizedBox(height: 12),
                    ],
                    const SizedBox(height: 8),
                  ],
                ],
              );
            },
            loading: () => const Center(child: Padding(padding: EdgeInsets.all(40), child: CircularProgressIndicator())),
            error: (error, _) => Text('Couldn\'t load games: $error', style: AppTextStyles.body(color: AppColors.neg)),
          ),
        ],
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
          border: Border.all(color: selected ? AppColors.cyan : AppColors.border),
          borderRadius: BorderRadius.circular(999),
        ),
        child: Text(
          label,
          style: AppTextStyles.microLabel(color: selected ? AppColors.cyan : AppColors.inkMute),
        ),
      ),
    );
  }
}
