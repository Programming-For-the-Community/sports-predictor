import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/data/events_repository.dart';
import '../../core/data/live_scores_repository.dart';
import '../../core/models/event.dart';
import '../../core/models/event_status.dart';
import '../../core/models/live_score.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/conference_filter_field.dart';
import '../../core/widgets/game_row.dart';
import '../../core/widgets/status_toggle.dart';
import '../../static/conference_order.dart';

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
/// string for anything that doesn't parse. kickoff_time comes back from
/// ESPN as UTC, so a late-evening US kickoff can land after midnight UTC;
/// toLocal() (same as game_row.dart's _kickoffTimeLabel) puts the heading
/// on the viewer's actual calendar day instead of UTC's.
String _dateHeading(String isoDateOrTimestamp) {
  final date = DateTime.tryParse(isoDateOrTimestamp)?.toLocal();
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

// Home's conference stands in for the whole matchup -- the away side can
// differ (a non-conference game), but grouping needs exactly one key per
// event. Null for NFL (no conference field on its team entities) and for
// any NCAAFB game whose home participant hasn't been enriched.
String? _primaryConference(SportEvent event) => event.home.conference ?? event.away.conference;

/// Buckets an already-date-sorted event list into (conference, dateGroups)
/// sections, Power 5 first then Group of 5 (see static/conference_order.
/// dart) with anything unclassified ("Other") last. Skips the outer
/// grouping entirely (one null-keyed section) when nothing in the list has
/// a conference at all. `filter`, when non-empty, keeps only conferences
/// whose own name contains it (case-insensitive).
List<(String?, List<(String, List<SportEvent>)>)> _groupByConferenceThenDate(List<SportEvent> events, String filter) {
  if (!events.any((e) => _primaryConference(e) != null)) {
    return [(null, _groupByDate(events))];
  }

  final byConference = <String, List<SportEvent>>{};
  for (final event in events) {
    byConference.putIfAbsent(_primaryConference(event) ?? 'Other', () => []).add(event);
  }
  final needle = filter.trim().toLowerCase();
  final conferences = byConference.keys.where((c) => needle.isEmpty || c.toLowerCase().contains(needle)).toList()
    ..sort((a, b) {
      if (a == 'Other') return b == 'Other' ? 0 : 1;
      if (b == 'Other') return -1;
      return compareConferenceOrder(a, b);
    });
  return [for (final conference in conferences) (conference, _groupByDate(byConference[conference]!))];
}

class EventListPage extends ConsumerStatefulWidget {
  const EventListPage({super.key, required this.sportId});

  final String sportId;

  @override
  ConsumerState<EventListPage> createState() => _EventListPageState();
}

class _EventListPageState extends ConsumerState<EventListPage> {
  String _status = EventStatus.scheduled;
  String _conferenceFilter = '';
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

  // Only while showing Upcoming. Torn down and rebuilt on every status
  // change so switching to Completed actually stops the ticking.
  void _scheduleLiveScoresPoll() {
    _liveScoresTimer?.cancel();
    if (_status != EventStatus.scheduled) return;
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
    // Only fetched/watched for the Upcoming tab.
    final liveScores = _status == EventStatus.scheduled
        ? ref.watch(liveScoresProvider(widget.sportId)).value ?? const <String, LiveEventState>{}
        : const <String, LiveEventState>{};

    return RefreshIndicator(
      onRefresh: () => ref.refresh(eventsListProvider((sport: widget.sportId, status: _status)).future),
      child: SingleChildScrollView(
      padding: const EdgeInsets.all(24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Wrap, not Row -- 'Upcoming/Current' is long enough that a
          // fixed unwrapping Row overflows at the narrowest supported
          // mobile widths; Wrap lets the second pill flow to its own
          // line instead of clipping.
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              StatusToggle(
                label: StatusToggleLabels.upcoming,
                selected: _status == EventStatus.scheduled,
                onTap: () => _setStatus(EventStatus.scheduled),
                accentColor: AppColors.cyan,
              ),
              StatusToggle(
                label: StatusToggleLabels.completed,
                selected: _status == EventStatus.completed,
                onTap: () => _setStatus(EventStatus.completed),
                accentColor: AppColors.cyan,
              ),
            ],
          ),
          const SizedBox(height: 8),
          // Stated once for the whole list rather than repeated on every
          // GameRow.
          Text('Times shown in your local time (${localTimezoneLabel()})', style: AppTextStyles.microLabel()),
          const SizedBox(height: 12),
          events.when(
            data: (list) {
              if (list.isEmpty) {
                // An empty "scheduled" list means next week hasn't been
                // ingested yet, not that there's nothing to show.
                final message = _status == EventStatus.scheduled ? 'Coming Soon' : 'No games found.';
                return Text(message, style: AppTextStyles.body(color: AppColors.inkSub));
              }
              // Soonest-first for Upcoming, most-recent-first for
              // Completed -- both read top-to-bottom as "closest to now
              // at the top".
              final sorted = [...list]..sort(
                  (a, b) => _status == EventStatus.scheduled
                      ? _sortKey(a).compareTo(_sortKey(b))
                      : _sortKey(b).compareTo(_sortKey(a)),
                );
              final grouped = _groupByConferenceThenDate(sorted, _conferenceFilter);
              return Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // Only shown when there's more than one conference to
                  // filter.
                  if (_groupByConferenceThenDate(sorted, '').length > 1) ...[
                    ConferenceFilterField(
                      value: _conferenceFilter,
                      onChanged: (value) => setState(() => _conferenceFilter = value),
                    ),
                    const SizedBox(height: 16),
                  ],
                  if (grouped.isEmpty)
                    Text('No conferences match "$_conferenceFilter".', style: AppTextStyles.body(color: AppColors.inkSub)),
                  for (final (conference, dateGroups) in grouped) ...[
                    if (conference != null) ...[
                      Padding(
                        padding: const EdgeInsets.only(bottom: 8),
                        child: Text(conference.toUpperCase(), style: AppTextStyles.sectionTitle(color: AppColors.cyan)),
                      ),
                    ],
                    for (final (heading, dayEvents) in dateGroups) ...[
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
                    if (conference != null) const SizedBox(height: 12),
                  ],
                ],
              );
            },
            loading: () => const Center(child: Padding(padding: EdgeInsets.all(40), child: CircularProgressIndicator())),
            error: (error, _) => Text('Couldn\'t load games: $error', style: AppTextStyles.body(color: AppColors.neg)),
          ),
        ],
      ),
      ),
    );
  }
}
