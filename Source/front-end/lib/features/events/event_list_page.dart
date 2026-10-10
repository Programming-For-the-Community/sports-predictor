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
import '../../core/widgets/game_row.dart';
import '../../core/widgets/pick_labels.dart';
import '../../core/widgets/status_toggle.dart';
import '../../static/conference_order.dart';
import '../../static/confidence_tiers.dart';
import 'event_list_filters.dart';

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

// Home's conference stands in for the whole matchup, since grouping needs
// one key per event. Null for a sport whose teams carry no conference.
String? _primaryConference(SportEvent event) => event.home.conference ?? event.away.conference;

const _otherConference = 'Other';

String _conferenceKey(SportEvent event) => _primaryConference(event) ?? _otherConference;

/// Buckets an already-date-sorted event list into (conference, dateGroups)
/// sections, Power 5 first then Group of 5 (see static/conference_order.dart)
/// with anything unclassified last. One null-keyed section when nothing in
/// the list has a conference.
List<(String?, List<(String, List<SportEvent>)>)> _groupByConferenceThenDate(List<SportEvent> events) {
  if (!events.any((e) => _primaryConference(e) != null)) {
    return [(null, _groupByDate(events))];
  }

  final byConference = <String, List<SportEvent>>{};
  for (final event in events) {
    byConference.putIfAbsent(_conferenceKey(event), () => []).add(event);
  }
  final conferences = byConference.keys.toList()
    ..sort((a, b) {
      if (a == _otherConference) return b == _otherConference ? 0 : 1;
      if (b == _otherConference) return -1;
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

class _EventListPageState extends ConsumerState<EventListPage> with WidgetsBindingObserver {
  String _status = EventStatus.scheduled;
  // An empty set filters nothing. Conference applies to both tabs, the
  // rest to Upcoming/Current only.
  final Set<String> _conferences = {};
  final Set<ConfidenceTier> _confidenceTiers = {};
  final Set<int> _weekdays = {};
  final Set<int> _kickoffHours = {};
  Timer? _liveScoresTimer;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _scheduleLiveScoresPoll();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _liveScoresTimer?.cancel();
    super.dispose();
  }

  // Browsers (and mobile OSes) throttle -- or outright pause -- a
  // backgrounded tab's own timers, so Timer.periodic below can silently
  // stop firing at its real 30s cadence the moment this tab loses focus,
  // with nothing bringing it back on its own once the tab is foregrounded
  // again. Without this, an event that actually went live while the tab
  // sat in the background kept showing its pre-game state for a while
  // after coming back -- a real complaint 2026-09-xx ("leave the site
  // open a while before events start... doesn't register it right
  // away"). AppLifecycleState.resumed fires on Flutter Web too, driven by
  // the page's own visibility state, not just a native app switch.
  //
  // Also invalidates eventsListProvider itself, not just liveScoresProvider
  // -- a real complaint confirmed live 2026-09-13 ("live-scores aren't
  // always up-to-date" after locking the machine/switching tabs a while).
  // GameRow falls back to this event's own stored result (event.home/
  // away.result?.score) once liveState stops carrying it (see GameRow's
  // own isLive/liveFinished comment: the live-scores cache lets go of an
  // event once our own storage's status catches up to "completed", up to
  // ~24h after the real game ends). Refreshing liveScoresProvider alone
  // then returns nothing for that event id, but the event list itself was
  // still the OLD "scheduled, not yet played" snapshot fetched at page
  // load -- so a game that fully finished while this tab sat backgrounded
  // could revert to looking like it hadn't started at all (score and
  // FINAL pill both gone) the moment it aged out of the live cache,
  // instead of picking up its own now-completed result.
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed && _status == EventStatus.scheduled) {
      ref.invalidate(liveScoresProvider(widget.sportId));
      ref.invalidate(eventsListProvider((sport: widget.sportId, status: EventStatus.scheduled)));
    }
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

  /// The day, with the model's record on it once any of its games is graded.
  Widget _dayHeading(String heading, List<SportEvent> dayEvents) {
    final graded = [for (final event in dayEvents) if (event.predictionComparison != null) event.predictionComparison!.correct];
    final hits = graded.where((correct) => correct).length;
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        children: [
          Expanded(child: Text(heading.toUpperCase(), style: AppTextStyles.microLabel(color: AppColors.inkSub))),
          if (graded.isNotEmpty)
            Text('MODEL $hits–${graded.length - hits}', style: AppTextStyles.microLabel(color: AppColors.inkSub)),
        ],
      ),
    );
  }

  List<Widget> _groupedSections(
    List<(String?, List<(String, List<SportEvent>)>)> grouped,
    Map<String, LiveEventState> liveScores,
  ) {
    return [
      for (final (conference, dateGroups) in grouped) ...[
        if (conference != null)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Text(conference.toUpperCase(), style: AppTextStyles.sectionTitle(color: AppColors.cyan)),
          ),
        for (final (heading, dayEvents) in dateGroups) ...[
          _dayHeading(heading, dayEvents),
          for (final event in dayEvents) ...[
            GameRow(sport: widget.sportId, event: event, liveState: liveScores[event.eventId]),
            const SizedBox(height: 12),
          ],
          const SizedBox(height: 8),
        ],
        if (conference != null) const SizedBox(height: 12),
      ],
    ];
  }

  void _toggle<T>(Set<T> selection, T value) {
    setState(() => selection.contains(value) ? selection.remove(value) : selection.add(value));
  }

  void _clearFilters() => setState(() {
        _conferences.clear();
        _confidenceTiers.clear();
        _weekdays.clear();
        _kickoffHours.clear();
      });

  /// The events passing the filters, plus how many were kept only because
  /// their prediction hasn't loaded yet (no tier to test). Watches the same
  /// per-event prediction providers GameRow does, so a confidence filter
  /// costs no extra requests.
  (List<SportEvent>, int) _applyFilters(List<SportEvent> events, bool isUpcoming) {
    var pending = 0;
    final kept = <SportEvent>[];
    for (final event in events) {
      if (_conferences.isNotEmpty && !_conferences.contains(_conferenceKey(event))) continue;
      if (isUpcoming) {
        if (_weekdays.isNotEmpty && !_weekdays.contains(eventWeekday(event))) continue;
        if (_kickoffHours.isNotEmpty && !_kickoffHours.contains(kickoffHour(event))) continue;
        if (_confidenceTiers.isNotEmpty) {
          final prediction = ref.watch(eventPredictionProvider((sport: widget.sportId, eventId: event.eventId))).value;
          if (prediction == null) {
            pending++;
          } else if (!_confidenceTiers.contains(ConfidenceTier.forProbability(prediction.homeWinProbability))) {
            continue;
          }
        }
      }
      kept.add(event);
    }
    return (kept, pending);
  }

  FilterGroup _group<T>(String label, Set<T> selection, List<(T, String, int?)> options) => FilterGroup(
        label: label,
        choices: [
          for (final (value, text, count) in options)
            FilterChoice(
              label: text, count: count,
              isSelected: () => selection.contains(value), onToggle: () => _toggle(selection, value),
            ),
        ],
      );

  /// A group with a single value has nothing to choose between and is left out.
  List<FilterGroup> _filterGroups(List<SportEvent> events, bool isUpcoming) {
    final conferences = events.map(_primaryConference).any((c) => c != null)
        ? [for (final (conference, _) in _groupByConferenceThenDate(events)) conference!]
        : const <String>[];
    final weekdays = eventWeekdays(events);
    final slots = kickoffSlots(events);
    int count(bool Function(SportEvent) test) => events.where(test).length;
    return [
      if (isUpcoming)
        _group('CONFIDENCE', _confidenceTiers, [for (final tier in ConfidenceTier.values) (tier, tier.label, null)]),
      if (isUpcoming && weekdays.length > 1)
        _group('DAY', _weekdays, [
          for (final day in weekdays) (day, weekdayLabel(day), count((e) => eventWeekday(e) == day)),
        ]),
      if (isUpcoming && slots.length > 1)
        _group('START', _kickoffHours, [
          for (final slot in slots) (slot.hour, slot.label, count((e) => kickoffHour(e) == slot.hour)),
        ]),
      if (conferences.length > 1)
        _group('CONFERENCE', _conferences, [
          for (final conference in conferences) (conference, conference, count((e) => _conferenceKey(e) == conference)),
        ]),
    ];
  }

  Widget _dataView(List<SportEvent> list, Map<String, LiveEventState> liveScores) {
    if (list.isEmpty) {
      // An empty "scheduled" list means next week hasn't been ingested
      // yet, not that there's nothing to show.
      final message = _status == EventStatus.scheduled ? 'Coming Soon' : 'No games found.';
      return Text(message, style: AppTextStyles.body(color: AppColors.inkSub));
    }
    // Soonest-first for Upcoming, most-recent-first for Completed -- both
    // read top-to-bottom as "closest to now at the top".
    final isUpcoming = _status == EventStatus.scheduled;
    final sorted = [...list]..sort(
        (a, b) => isUpcoming ? _sortKey(a).compareTo(_sortKey(b)) : _sortKey(b).compareTo(_sortKey(a)),
      );
    final (filtered, pending) = _applyFilters(sorted, isUpcoming);
    final groups = _filterGroups(sorted, isUpcoming);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (groups.isNotEmpty) ...[
          EventListFilters(groups: groups, shown: filtered.length, total: sorted.length, onClear: _clearFilters),
          const SizedBox(height: 16),
        ],
        if (pending > 0) ...[
          Text(
            '$pending ${pending == 1 ? 'game' : 'games'} still loading a prediction -- shown until its confidence is known.',
            style: AppTextStyles.microLabel(),
          ),
          const SizedBox(height: 12),
        ],
        if (filtered.isEmpty) Text('No games match these filters.', style: AppTextStyles.body(color: AppColors.inkSub)),
        ..._groupedSections(_groupByConferenceThenDate(filtered), liveScores),
      ],
    );
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
          Wrap(
            spacing: 16,
            runSpacing: 6,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text('Times shown in your local time (${localTimezoneLabel()})', style: AppTextStyles.microLabel()),
              const ActualModelKey(),
            ],
          ),
          const SizedBox(height: 12),
          events.when(
            data: (list) => _dataView(list, liveScores),
            loading: () => const Center(child: Padding(padding: EdgeInsets.all(40), child: CircularProgressIndicator())),
            error: (error, _) => Text('Couldn\'t load games: $error', style: AppTextStyles.body(color: AppColors.neg)),
          ),
        ],
      ),
      ),
    );
  }
}
