import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/events_repository.dart';
import 'package:front_end/core/data/live_scores_repository.dart';
import 'package:front_end/core/models/event.dart';
import 'package:front_end/core/models/live_score.dart';
import 'package:front_end/features/events/event_list_page.dart';

SportEvent _scheduledEvent(String id, String kickoff) => SportEvent(
      eventId: id,
      eventDate: kickoff.split('T').first,
      kickoffTime: kickoff,
      status: 'scheduled',
      week: 2,
      round: null,
      participants: const [
        Participant(entityId: '12', role: 'home', result: null),
        Participant(entityId: '13', role: 'away', result: null),
      ],
      predictionComparison: null,
      leadersComparison: null,
    );

void main() {
  testWidgets('refreshes the scheduled event list on resume, not just live scores', (tester) async {
    // Regression, confirmed live 2026-09-13 ("live-scores aren't always
    // up-to-date" after locking the machine/switching tabs a while).
    // GameRow falls back to this event's own stored result once
    // liveState stops carrying it (the live-scores cache lets go of an
    // event once our own storage's status catches up to "completed", up
    // to ~24h after the real game ends) -- refreshing liveScoresProvider
    // alone then returns nothing for that event id, but a stale cached
    // event list here still shows the old pre-game snapshot, so a game
    // that finished while backgrounded could revert to looking like it
    // hadn't started at all instead of picking up its own real result.
    var eventsListCalls = 0;

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          eventsListProvider.overrideWith((ref, query) async {
            if (query.status == 'scheduled') eventsListCalls++;
            return query.status == 'scheduled' ? [_scheduledEvent('401547417', '2026-09-14T17:00:00Z')] : [];
          }),
          liveScoresProvider.overrideWith((ref, sport) async => const <String, LiveEventState>{}),
        ],
        child: const MaterialApp(home: Scaffold(body: EventListPage(sportId: 'nfl'))),
      ),
    );
    await tester.pumpAndSettle();
    final initialCalls = eventsListCalls;

    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pumpAndSettle();

    expect(eventsListCalls, greaterThan(initialCalls));
  });

  testWidgets('does not refresh the event list on resume while showing Completed', (tester) async {
    // Mirrors the existing liveScoresProvider poll's own scoping -- only
    // the Upcoming/Current tab has anything live to catch up on.
    var eventsListCalls = 0;

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          eventsListProvider.overrideWith((ref, query) async {
            eventsListCalls++;
            return query.status == 'scheduled' ? [_scheduledEvent('401547417', '2026-09-14T17:00:00Z')] : [];
          }),
          liveScoresProvider.overrideWith((ref, sport) async => const <String, LiveEventState>{}),
        ],
        child: const MaterialApp(home: Scaffold(body: EventListPage(sportId: 'nfl'))),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.text('Completed'));
    await tester.pumpAndSettle();
    final callsOnCompletedTab = eventsListCalls;

    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pumpAndSettle();

    expect(eventsListCalls, callsOnCompletedTab);
  });
}
