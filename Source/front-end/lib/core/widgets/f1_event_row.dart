import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/f1_events_repository.dart';
import '../models/event_status.dart';
import '../models/f1_event.dart';
import '../models/f1_live_score.dart';
import '../models/f1_prediction.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';
import 'event_card.dart';
import 'live_status_pill.dart';
import 'sprint_badge.dart';

const _weekdayAbbreviations = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

DateTime? _date(F1Event event) => DateTime.tryParse(event.eventDate);

abstract final class F1SessionLabels {
  static const sprint = 'Sprint';
  static const grandPrix = 'Grand Prix';

  static String of(bool isSprint) => isSprint ? sprint : grandPrix;
}

/// "SPRINT · SAT OCT 17" -- which session, and the day it runs.
String f1SessionLabel(F1Event event) {
  final name = F1SessionLabels.of(event.isSprint).toUpperCase();
  final date = _date(event);
  if (date == null) return name;
  return '$name · ${_weekdayAbbreviations[date.weekday - 1]} ${monthAbbreviations[date.month - 1]} ${date.day}'.toUpperCase();
}

/// The sessions of one race weekend that share a list -- the sprint and the
/// Grand Prix, earliest first, or just one of them.
class F1Weekend {
  const F1Weekend(this.sessions);

  final List<F1Event> sessions;

  F1Event get first => sessions.first;
  F1Event get last => sessions.last;

  /// The Grand Prix when the weekend has one, else its only session.
  F1Event get main => sessions.firstWhere((event) => !event.isSprint, orElse: () => first);

  String get name => main.raceName ?? F1SessionLabels.grandPrix;

  bool get hasBothSessions => sessions.length > 1;

  /// The session a tap on the whole card opens: the first not yet stored as
  /// completed, else the last one.
  F1Event get next => sessions.firstWhere((event) => event.status != EventStatus.completed, orElse: () => last);

  List<EventSession> get chips => hasBothSessions
      ? [for (final event in sessions) EventSession(eventId: event.eventId, label: f1SessionLabel(event))]
      : const [];
}

/// Groups `events` (already in display order) into weekends by season and
/// round, keeping each weekend where its first session falls. An event with
/// no round stands alone.
List<F1Weekend> groupF1Weekends(List<F1Event> events) {
  final weekends = <F1Weekend>[];
  final byRound = <(int, int), List<F1Event>>{};
  for (final event in events) {
    final season = event.season;
    final round = event.week;
    if (season == null || round == null) {
      weekends.add(F1Weekend([event]));
      continue;
    }
    final sessions = byRound[(season, round)];
    if (sessions != null) {
      sessions.add(event);
      continue;
    }
    final created = [event];
    byRound[(season, round)] = created;
    weekends.add(F1Weekend(created));
  }
  for (final sessions in byRound.values) {
    // Sprint before the Grand Prix when both carry the same date.
    sessions.sort((a, b) {
      final byDate = a.eventDate.compareTo(b.eventDate);
      if (byDate != 0) return byDate;
      return (a.isSprint ? 0 : 1).compareTo(b.isSprint ? 0 : 1);
    });
  }
  return weekends;
}

/// One race weekend in GET /f1/events' list. A sprint weekend is one card
/// whose two session chips each open their own page; a lone sprint keeps
/// its SPRINT badge.
class F1EventRow extends StatelessWidget {
  const F1EventRow({super.key, required this.sport, required this.weekend, this.liveScores = const {}, this.now});

  final String sport;
  final F1Weekend weekend;
  // From f1LiveScoresProvider, by event id; empty outside a live window.
  final Map<String, F1LiveEventState> liveScores;
  final DateTime? now;

  Widget? _trailing() {
    if (weekend.sessions.any((event) => liveScores[event.eventId]?.isLive ?? false)) return const LiveStatusPill();
    final start = _date(weekend.first);
    if (start == null) return null;
    // A session ESPN reports over can still be stored as scheduled for a day.
    final finished = weekend.sessions.every(
      (event) => event.status == EventStatus.completed || (liveScores[event.eventId]?.isFinished ?? false),
    );
    if (finished) return Text('${start.year}', style: AppTextStyles.microLabel(color: AppColors.inkSub));
    final startsIn = startsInLabel(start, now ?? DateTime.now());
    return startsIn == null ? null : Text(startsIn, style: AppTextStyles.microLabel(color: AppColors.inkSub));
  }

  @override
  Widget build(BuildContext context) {
    final start = _date(weekend.first);
    return EventCard(
      sport: sport,
      eventId: weekend.next.eventId,
      title: Wrap(
        spacing: 8,
        runSpacing: 4,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          Text(weekend.name, style: AppTextStyles.body(color: AppColors.ink)),
          if (!weekend.hasBothSessions && weekend.first.isSprint) const SprintBadge(),
        ],
      ),
      dateBlock: start == null ? null : dateBlockLabels(start, _date(weekend.last)),
      venueLabel: weekend.main.venueLabel,
      trailing: _trailing(),
      sessions: weekend.chips,
    );
  }
}

/// The next race weekend, with the three drivers the model ranks highest
/// for its next session and each one's chance of winning it.
class FeaturedF1Weekend extends ConsumerWidget {
  const FeaturedF1Weekend({super.key, required this.sport, required this.weekend, this.liveScores = const {}, this.now});

  final String sport;
  final F1Weekend weekend;
  final Map<String, F1LiveEventState> liveScores;
  final DateTime? now;

  static String _eyebrow(String? startsIn, {required bool isLive}) {
    if (isLive) return LiveStatusPill.label;
    return startsIn == null ? 'NEXT' : 'NEXT · $startsIn';
  }

  static String picksLabel(bool isSprint) => isSprint ? 'SPRINT WIN CHANCE' : 'WIN CHANCE';

  static List<FeaturedPick> _picks(F1EventPrediction prediction) => [
        for (final driver in prediction.field.take(3))
          if (driver.winProbability != null)
            FeaturedPick(
              name: driver.name ?? driver.entityId,
              detail: driver.constructorName,
              value: '${(driver.winProbability!.value * 100).round()}%',
            ),
      ];

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final session = weekend.next;
    final start = _date(session);
    final isLive = liveScores[session.eventId]?.isLive ?? false;
    final startsIn = start == null ? null : startsInLabel(start, now ?? DateTime.now());
    final prediction = ref.watch(f1EventPredictionProvider((sport: sport, eventId: session.eventId)));
    return FeaturedEventCard(
      sport: sport,
      eventId: session.eventId,
      eyebrow: _eyebrow(startsIn, isLive: isLive),
      eyebrowColor: isLive ? AppColors.live : AppColors.violet,
      title: weekend.name,
      subtitle: weekend.hasBothSessions ? null : f1SessionLabel(session),
      venueLabel: weekend.main.venueLabel,
      sessions: weekend.chips,
      picksLabel: picksLabel(session.isSprint),
      picks: prediction.whenData(_picks),
    );
  }
}
