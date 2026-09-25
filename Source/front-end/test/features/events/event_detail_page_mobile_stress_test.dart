import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/misc.dart' show Override;
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

import 'package:front_end/core/data/events_repository.dart';
import 'package:front_end/core/data/live_scores_repository.dart';
import 'package:front_end/core/models/event.dart';
import 'package:front_end/core/models/event_leaders.dart';
import 'package:front_end/core/models/live_score.dart';
import 'package:front_end/core/models/prediction.dart';
import 'package:front_end/features/events/event_detail_page.dart';
import 'package:front_end/features/sport_shell/sport_shell_page.dart';

import '../../support/mobile_viewport.dart' show loadAppFonts;


// Worst-case content (long names/venue, several candidates per category,
// double-digit stats) at narrow widths AND enlarged system text -- the
// sparse fixtures in event_detail_page_mobile_test.dart pass even where a
// real game overflows.
const _widths = [320.0, 360.0, 390.0, 412.0];
const _textScales = [1.0, 1.3, 1.6];

SportEvent _event({required String status, bool completed = false, PredictionComparison? comparison, EventLeadersComparison? leaders}) => SportEvent(
      eventId: '1',
      eventDate: '2026-09-27',
      kickoffTime: '2026-09-27T17:00:00Z',
      status: status,
      week: 3,
      round: null,
      venueName: 'Mercedes-Benz Superdome Stadium',
      venueCity: 'East Rutherford',
      venueState: 'New Jersey',
      participants: [
        Participant(entityId: '2', role: 'home', result: completed ? const ParticipantResult(score: 127, won: true) : null),
        Participant(entityId: '3', role: 'away', result: completed ? const ParticipantResult(score: 103, won: false) : null),
      ],
      predictionComparison: comparison,
      leadersComparison: leaders,
    );

const _longPlayers = [
  PlayerStatLine(entityId: '1', name: 'Christian McCaffrey-Williamson Jr.', stats: {'passing_yards': 412, 'passing_touchdowns': 5, 'rushing_yards': 112, 'rushing_touchdowns': 2, 'receiving_yards': 187, 'receiving_touchdowns': 3, 'defensive_sacks': 2.5}),
  PlayerStatLine(entityId: '2', name: 'DeAndre Hopkins-Wilson III', stats: {'passing_yards': 312, 'passing_touchdowns': 3, 'rushing_yards': 96, 'rushing_touchdowns': 1, 'receiving_yards': 118, 'receiving_touchdowns': 1, 'defensive_sacks': 1.5}),
  PlayerStatLine(entityId: '3', name: 'Amon-Ra St. Brown', stats: {'passing_yards': 289, 'passing_touchdowns': 2, 'rushing_yards': 88, 'rushing_touchdowns': 1, 'receiving_yards': 101, 'receiving_touchdowns': 1, 'defensive_sacks': 1.0}),
];

const _leaders = EventLeaders(
  home: TeamLeaders({'passing': _longPlayers, 'rushing': _longPlayers, 'receiving': _longPlayers, 'sacks': _longPlayers}),
  away: TeamLeaders({'passing': _longPlayers, 'rushing': _longPlayers, 'receiving': _longPlayers, 'sacks': _longPlayers}),
);

const _prediction = EventPrediction(
  homeWinProbability: 0.62, homeWinProbabilityModelVersion: 9, margin: 14.5, homeScore: 127.3, awayScore: 122.8,
  leaders: _leaders,
);

List<PlayerStatLineComparison> _cmp() => [
      for (final p in _longPlayers)
        PlayerStatLineComparison(
          entityId: p.entityId, name: p.name,
          predicted: {for (final e in p.stats.entries) e.key: e.value}, actual: {for (final e in p.stats.entries) e.key: e.value + 11},
        ),
    ];

// The real app hosts the page inside SportShellPage (back button, title,
// tab toggle) on a real phone-height viewport -- not a 1200px-tall bare
// Scaffold.
Widget _app(List<Override> overrides, double scale) {
  final router = GoRouter(
    initialLocation: '/nfl/events/1',
    routes: [
      ShellRoute(
        builder: (context, state, child) => SportShellPage(sportId: 'nfl', child: child),
        routes: [
          GoRoute(path: '/nfl/events/:id', builder: (context, state) => const EventDetailPage(sportId: 'nfl', eventId: '1')),
        ],
      ),
    ],
  );
  return ProviderScope(
    overrides: overrides,
    child: MaterialApp.router(
      routerConfig: router,
      builder: (context, child) => MediaQuery(data: MediaQuery.of(context).copyWith(textScaler: TextScaler.linear(scale)), child: child!),
    ),
  );
}

Future<void> pumpAtWidth(WidgetTester tester, double width, Widget widget) async {
  tester.view.physicalSize = Size(width, 640);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  await tester.runAsync(loadAppFonts);
  await tester.pumpWidget(widget);
  await tester.pumpAndSettle();
}

// An ellipsis never throws, so takeException() alone can't see a clipped
// player name or stat line -- scan every rendered paragraph for one that
// was cut off by maxLines. `allowed` lists text that's intentionally
// single-line/ellipsized (none currently).
List<String> _truncatedText(WidgetTester tester) {
  final clipped = <String>[];
  void visit(RenderObject node) {
    if (node is RenderParagraph && node.didExceedMaxLines) clipped.add(node.text.toPlainText());
    node.visitChildren(visit);
  }
  visit(tester.binding.renderViews.first);
  return clipped;
}

void main() {
  for (final width in _widths) {
    for (final scale in _textScales) {
      final tag = '${width.toInt()}px @ ${scale}x text';

      testWidgets('upcoming with full leaders + venue: $tag', (tester) async {
        await pumpAtWidth(tester, width, _app([
          eventsListProvider.overrideWith((ref, q) async => q.status == 'scheduled' ? [_event(status: 'scheduled')] : []),
          eventPredictionProvider.overrideWith((ref, q) async => _prediction),
          liveScoresProvider.overrideWith((ref, s) async => const {}),
        ], scale));
        expect(tester.takeException(), isNull);
        expect(_truncatedText(tester), isEmpty, reason: 'text clipped with an ellipsis');
      });

      testWidgets('live with live leaders comparison: $tag', (tester) async {
        await pumpAtWidth(tester, width, _app([
          eventsListProvider.overrideWith((ref, q) async => q.status == 'scheduled' ? [_event(status: 'scheduled')] : []),
          eventPredictionProvider.overrideWith((ref, q) async => _prediction),
          liveScoresProvider.overrideWith((ref, s) async => {
                '1': LiveEventState(live: true, detail: 'End of 3rd Quarter', homeScore: 127, awayScore: 103, playerStats: {
                  for (final p in _longPlayers) p.entityId: p.stats,
                }),
              }),
        ], scale));
        expect(tester.takeException(), isNull);
        expect(_truncatedText(tester), isEmpty, reason: 'text clipped with an ellipsis');
      });

      testWidgets('completed with full comparison + venue: $tag', (tester) async {
        final comparison = TeamLeadersComparison({'passing': _cmp(), 'rushing': _cmp(), 'receiving': _cmp(), 'sacks': _cmp()});
        await pumpAtWidth(tester, width, _app([
          eventsListProvider.overrideWith((ref, q) async => q.status == 'completed'
              ? [
                  _event(
                    status: 'completed', completed: true, leaders: EventLeadersComparison(home: comparison, away: comparison),
                    comparison: const PredictionComparison(
                      predictedHomeWinProbability: 0.62, predictedHomeWon: true, actualHomeWon: true, correct: true,
                      predictedMargin: 14.5, actualMargin: 24, predictedHomeScore: 127.3, predictedAwayScore: 122.8,
                      actualHomeScore: 127, actualAwayScore: 103,
                    ),
                  ),
                ]
              : []),
        ], scale));
        expect(tester.takeException(), isNull);
        expect(_truncatedText(tester), isEmpty, reason: 'text clipped with an ellipsis');
      });
    }
  }
}
