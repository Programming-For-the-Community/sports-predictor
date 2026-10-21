import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/events_repository.dart';
import 'package:front_end/core/data/live_scores_repository.dart';
import 'package:front_end/core/models/event.dart';
import 'package:front_end/core/models/live_score.dart';
import 'package:front_end/core/models/prediction.dart';
import 'package:front_end/core/models/upset.dart';
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

SportEvent _conferenceEvent(String id, String kickoff, String? conference, {String status = 'scheduled'}) => SportEvent(
      eventId: id,
      eventDate: kickoff.split('T').first,
      kickoffTime: kickoff,
      status: status,
      week: 2,
      round: null,
      participants: [
        Participant(entityId: 'h$id', role: 'home', result: null, conference: conference),
        Participant(entityId: 'a$id', role: 'away', result: null),
      ],
      predictionComparison: null,
      leadersComparison: null,
    );

Widget _page(Future<List<SportEvent>> Function(String status) events, {void Function()? onLiveScores}) => ProviderScope(
      overrides: [
        eventsListProvider.overrideWith((ref, query) => events(query.status)),
        liveScoresProvider.overrideWith((ref, sport) async {
          onLiveScores?.call();
          return const <String, LiveEventState>{};
        }),
      ],
      child: const MaterialApp(home: Scaffold(body: EventListPage(sportId: 'ncaafb'))),
    );

void main() {
  group('conference grouping', () {
    final events = [
      _conferenceEvent('1', '2026-09-14T17:00:00Z', 'SEC'),
      _conferenceEvent('2', '2026-09-14T18:00:00Z', null),
      _conferenceEvent('3', '2026-09-15T17:00:00Z', 'Big Ten'),
      _conferenceEvent('4', '2026-09-15T18:00:00Z', null),
    ];

    testWidgets('groups by conference with Other last, and the Conference menu narrows to the chosen ones', (tester) async {
      await tester.pumpWidget(_page((status) async => events));
      await tester.pumpAndSettle();

      final sec = tester.getTopLeft(find.text('SEC')).dy;
      final bigTen = tester.getTopLeft(find.text('BIG TEN')).dy;
      final other = tester.getTopLeft(find.text('OTHER')).dy;
      expect(other, greaterThan(sec));
      expect(other, greaterThan(bigTen));

      await tester.tap(find.text('CONFERENCE'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(CheckboxMenuButton, 'SEC'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('CONFERENCE · SEC'));
      await tester.pumpAndSettle();

      expect(find.text('SEC'), findsOneWidget);
      expect(find.text('BIG TEN'), findsNothing);
      expect(find.text('OTHER'), findsNothing);
      expect(find.text('1 OF 4 GAMES'), findsOneWidget);
    });

    testWidgets('the Completed tab keeps the Conference menu and drops the game filters', (tester) async {
      final completed = [
        _conferenceEvent('5', '2026-09-01T17:00:00Z', 'SEC', status: 'completed'),
        _conferenceEvent('6', '2026-09-08T17:00:00Z', 'Big Ten', status: 'completed'),
      ];
      await tester.pumpWidget(_page((status) async => status == 'completed' ? completed : events));
      await tester.pumpAndSettle();

      await tester.tap(find.text('Completed'));
      await tester.pumpAndSettle();

      expect(find.text('CONFERENCE'), findsOneWidget);
      expect(find.text('CONFIDENCE'), findsNothing);
    });

    testWidgets('Completed lists most-recent first, and switching back to Upcoming works', (tester) async {
      final completed = [
        _conferenceEvent('5', '2026-09-01T17:00:00Z', null, status: 'completed'),
        _conferenceEvent('6', '2026-09-08T17:00:00Z', null, status: 'completed'),
      ];
      await tester.pumpWidget(_page((status) async => status == 'completed' ? completed : events));
      await tester.pumpAndSettle();

      await tester.tap(find.text('Completed'));
      await tester.pumpAndSettle();
      expect(
        tester.getTopLeft(find.text('TUESDAY, SEP 8')).dy,
        lessThan(tester.getTopLeft(find.text('TUESDAY, SEP 1')).dy),
      );

      await tester.tap(find.text('Upcoming/Current'));
      await tester.pumpAndSettle();
      expect(find.text('SEC'), findsOneWidget);
    });
  });

  testWidgets('polls live scores every 30 seconds on the Upcoming tab', (tester) async {
    var liveScoreCalls = 0;
    await tester.pumpWidget(_page((status) async => [], onLiveScores: () => liveScoreCalls++));
    await tester.pumpAndSettle();
    final initial = liveScoreCalls;

    await tester.pump(const Duration(seconds: 31));
    await tester.pumpAndSettle();

    expect(liveScoreCalls, greaterThan(initial));
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('pull-to-refresh refetches the event list', (tester) async {
    var calls = 0;
    await tester.pumpWidget(_page((status) async {
      calls++;
      return [_scheduledEvent('401547417', '2026-09-14T17:00:00Z')];
    }));
    await tester.pumpAndSettle();
    final initial = calls;

    await tester.widget<RefreshIndicator>(find.byType(RefreshIndicator)).onRefresh();
    await tester.pumpAndSettle();

    expect(calls, greaterThan(initial));
  });

  testWidgets('shows the load error', (tester) async {
    await tester.pumpWidget(_page((status) async => throw Exception('boom')));
    await tester.pumpAndSettle();

    expect(find.textContaining("Couldn't load games: Exception: boom"), findsOneWidget);
  });

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

  testWidgets('refetches the event list once a finished game drops out of live scores', (tester) async {
    var eventsListCalls = 0;
    var liveScores = <String, LiveEventState>{
      '401547417': const LiveEventState(live: false, completed: true, detail: 'Final', homeScore: 104, awayScore: 116),
    };

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          eventsListProvider.overrideWith((ref, query) async {
            if (query.status == 'scheduled') eventsListCalls++;
            return [_scheduledEvent('401547417', '2026-09-14T17:00:00Z')];
          }),
          liveScoresProvider.overrideWith((ref, sport) async => liveScores),
        ],
        child: const MaterialApp(home: Scaffold(body: EventListPage(sportId: 'nba'))),
      ),
    );
    await tester.pumpAndSettle();

    await tester.pump(const Duration(seconds: 31));
    await tester.pumpAndSettle();
    final callsWhileCarried = eventsListCalls;

    liveScores = const {};
    await tester.pump(const Duration(seconds: 31));
    await tester.pumpAndSettle();

    expect(eventsListCalls, callsWhileCarried + 1);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('does not refetch the event list when a game that never went live is absent from live scores', (tester) async {
    var eventsListCalls = 0;
    var liveScores = <String, LiveEventState>{
      '401547417': const LiveEventState(live: false, detail: 'Pregame', homeScore: 0, awayScore: 0),
    };

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          eventsListProvider.overrideWith((ref, query) async {
            if (query.status == 'scheduled') eventsListCalls++;
            return [_scheduledEvent('401547417', '2026-09-14T17:00:00Z')];
          }),
          liveScoresProvider.overrideWith((ref, sport) async => liveScores),
        ],
        child: const MaterialApp(home: Scaffold(body: EventListPage(sportId: 'nba'))),
      ),
    );
    await tester.pumpAndSettle();
    final initialCalls = eventsListCalls;

    liveScores = const {};
    await tester.pump(const Duration(seconds: 31));
    await tester.pumpAndSettle();

    expect(eventsListCalls, initialCalls);
    await tester.pumpWidget(const SizedBox());
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

  group('upsets filter', () {
    SportEvent game(String id, String status, String favorite, {bool homeWon = true}) => SportEvent(
          eventId: id,
          eventDate: '2026-09-14',
          kickoffTime: '2026-09-14T17:00:00Z',
          status: status,
          week: 2,
          round: null,
          participants: [
            Participant(entityId: 'h$id', role: 'home', result: status == 'completed' ? ParticipantResult(score: homeWon ? 24 : 20, won: homeWon) : null),
            Participant(entityId: 'a$id', role: 'away', result: status == 'completed' ? ParticipantResult(score: homeWon ? 20 : 24, won: !homeWon) : null),
          ],
          predictionComparison: null,
          leadersComparison: null,
          bettingFavorite: favorite,
        );

    Widget page(List<SportEvent> scheduled, List<SportEvent> completed) => ProviderScope(
          overrides: [
            eventsListProvider.overrideWith((ref, query) async => query.status == 'completed' ? completed : scheduled),
            liveScoresProvider.overrideWith((ref, sport) async => const <String, LiveEventState>{}),
            // The model has the home side at 70% in every game.
            eventPredictionProvider.overrideWith((ref, query) async => const EventPrediction(
                  homeWinProbability: 0.7, homeWinProbabilityModelVersion: 1, margin: 5, homeScore: 24, awayScore: 19, leaders: null,
                )),
          ],
          child: const MaterialApp(home: Scaffold(body: EventListPage(sportId: 'nfl'))),
        );

    testWidgets('Upcoming offers only Upset watch, and it keeps just those games', (tester) async {
      // Game 1: books like the home side, as does the model. Game 2: books
      // like the away side the model gives 30% -- an upset watch.
      await tester.pumpWidget(page([game('1', 'scheduled', 'home'), game('2', 'scheduled', 'away')], const []));
      await tester.pumpAndSettle();

      await tester.tap(find.text('UPSETS'));
      await tester.pumpAndSettle();
      expect(find.widgetWithText(CheckboxMenuButton, UpsetFlag.watch.label), findsOneWidget);
      expect(find.widgetWithText(CheckboxMenuButton, UpsetFlag.upset.label), findsNothing);

      await tester.tap(find.widgetWithText(CheckboxMenuButton, UpsetFlag.watch.label));
      await tester.pumpAndSettle();
      expect(find.text('1 OF 2 GAMES'), findsOneWidget);
    });

    testWidgets('Completed offers only Upset, with its count, and it keeps just those games', (tester) async {
      await tester.pumpWidget(page(const [], [
        game('3', 'completed', 'home'),
        game('4', 'completed', 'home', homeWon: false),
      ]));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Completed'));
      await tester.pumpAndSettle();

      await tester.tap(find.text('UPSETS'));
      await tester.pumpAndSettle();
      expect(find.widgetWithText(CheckboxMenuButton, UpsetFlag.watch.label), findsNothing);
      final upset = find.widgetWithText(CheckboxMenuButton, UpsetFlag.upset.label);
      expect(upset, findsOneWidget);
      expect(find.descendant(of: upset, matching: find.text('1')), findsOneWidget);

      await tester.tap(upset);
      await tester.pumpAndSettle();
      expect(find.text('1 OF 2 GAMES'), findsOneWidget);
    });
  });
}
