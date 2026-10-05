import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/data/events_repository.dart';
import 'package:front_end/core/models/event.dart';
import 'package:front_end/core/models/event_leaders.dart';
import 'package:front_end/core/models/live_score.dart';
import 'package:front_end/core/models/prediction.dart';
import 'package:front_end/core/theme/app_colors.dart';
import 'package:front_end/core/widgets/game_row.dart';
import 'package:front_end/core/widgets/matchup_hero.dart';
import 'package:front_end/core/widgets/possession_ball.dart';
import 'package:front_end/core/widgets/team_leaders_panel.dart';
import 'package:front_end/core/widgets/win_probability_bar.dart';

// '12' = KC (home), '13' = LV (away) -- see nfl_team_colors.dart.
SportEvent _event() => SportEvent(
      eventId: '401547417',
      eventDate: '2026-10-04',
      kickoffTime: '2026-10-04T17:00:00Z',
      status: 'scheduled',
      week: 5,
      round: null,
      participants: const [
        Participant(entityId: '12', role: 'home', result: null),
        Participant(entityId: '13', role: 'away', result: null),
      ],
      predictionComparison: null,
      leadersComparison: null,
    );

const _prediction = EventPrediction(
  homeWinProbability: 0.68,
  homeWinProbabilityModelVersion: 3,
  margin: 6.5,
  homeScore: 27.4,
  awayScore: 20.9,
  leaders: null,
);

const _awayBall = FootballSituation(possession: 'away', downDistance: '3rd & 4', fieldPosition: 'KC 48');
const _homeRedZone = FootballSituation(possession: 'home', downDistance: '1st & Goal', fieldPosition: 'LV 6', redZone: true);

Future<void> _pumpRow(WidgetTester tester, LiveEventState liveState, {double width = 800}) async {
  tester.view.physicalSize = Size(width, 800);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(ProviderScope(
    overrides: [eventPredictionProvider.overrideWith((ref, query) async => _prediction)],
    child: MaterialApp(home: Scaffold(body: GameRow(sport: 'nfl', event: _event(), liveState: liveState))),
  ));
  await tester.pumpAndSettle();
}

Future<void> _pumpHero(WidgetTester tester, LiveEventState liveState) async {
  await tester.pumpWidget(MaterialApp(
    home: Scaffold(body: SingleChildScrollView(child: MatchupHero(sport: 'nfl', event: _event(), prediction: _prediction, liveState: liveState))),
  ));
}

/// The team abbreviation's position relative to the football.
Offset _ballCenter(WidgetTester tester) => tester.getCenter(find.byType(PossessionBall));

void main() {
  group('FootballSituation.fromJson', () {
    test('reads the live-scores situation', () {
      final state = LiveEventState.fromJson({
        'live': true,
        'detail': '3:56 - 1st',
        'home_score': 10,
        'away_score': 7,
        'situation': {'possession': 'away', 'down_distance': '3rd & 4', 'field_position': 'MIN 48', 'red_zone': false},
      });

      expect(state.situation?.awayHasBall, isTrue);
      expect(state.situation?.downDistance, '3rd & 4');
      expect(state.situation?.fieldPosition, 'MIN 48');
      expect(state.situation?.redZone, isFalse);
    });

    test('is null without a situation, and tolerates odd values', () {
      expect(LiveEventState.fromJson({'live': true, 'detail': 'Halftime', 'home_score': 10, 'away_score': 10}).situation, isNull);
      expect(FootballSituation.fromJson('nope'), isNull);
      final odd = FootballSituation.fromJson({'possession': 'both', 'down_distance': '', 'field_position': 48, 'red_zone': 'yes'})!;
      expect(odd.possession, isNull);
      expect(odd.hasDown, isFalse);
      expect(odd.redZone, isFalse);
    });
  });

  group('events list row', () {
    testWidgets('a live game shows the football by the team with the ball and the down beside the clock', (tester) async {
      await _pumpRow(tester, const LiveEventState(live: true, detail: 'Q1 3:56', homeScore: 10, awayScore: 7, situation: _awayBall));

      expect(find.byType(PossessionBall), findsOneWidget);
      // LV (away) is the first line, so the ball sits beside it.
      expect((_ballCenter(tester).dy - tester.getCenter(find.text('LV')).dy).abs(), lessThan(4));
      expect(find.textContaining('3rd & 4 · KC 48'), findsOneWidget);
      expect(find.text('Q1 3:56'), findsOneWidget);
    });

    testWidgets('inside the 20 it says Red zone', (tester) async {
      await _pumpRow(tester, const LiveEventState(live: true, detail: 'Q3 8:42', homeScore: 24, awayScore: 17, situation: _homeRedZone));

      expect(find.textContaining('Red zone'), findsOneWidget);
      expect((_ballCenter(tester).dy - tester.getCenter(find.text('KC')).dy).abs(), lessThan(4));
    });

    testWidgets('on a phone the down goes on its own line under the clock, with no overflow', (tester) async {
      await _pumpRow(tester, const LiveEventState(live: true, detail: 'Q3 8:42', homeScore: 24, awayScore: 17, situation: _homeRedZone), width: 360);

      expect(tester.takeException(), isNull);
      final clock = tester.getRect(find.text('Q3 8:42'));
      final down = tester.getRect(find.textContaining('1st & Goal'));
      expect(down.top, greaterThan(clock.bottom - 1));
    });

    testWidgets('no football or down at a break', (tester) async {
      await _pumpRow(tester, const LiveEventState(live: true, detail: 'Halftime', homeScore: 10, awayScore: 10));

      expect(find.byType(PossessionBall), findsNothing);
      expect(find.text('Halftime'), findsOneWidget);
    });

    testWidgets('a finished game says only FINAL', (tester) async {
      await _pumpRow(tester, const LiveEventState(live: false, completed: true, detail: 'Final', homeScore: 24, awayScore: 17));

      expect(find.text('FINAL'), findsOneWidget);
      expect(find.text('Final'), findsNothing);
      expect(find.byType(PossessionBall), findsNothing);
    });

    testWidgets('an upcoming game shows no status text, just the win-probability bar', (tester) async {
      await _pumpRow(tester, const LiveEventState(live: false, detail: '10/4 - 1:00 PM EDT', homeScore: 0, awayScore: 0));

      expect(find.byType(WinProbabilityBar), findsOneWidget);
      expect(find.text('10/4 - 1:00 PM EDT'), findsNothing);
      expect(find.text('FINAL'), findsNothing);
    });
  });

  group('event detail header', () {
    testWidgets('the football sits only by the team name, and the down line has none', (tester) async {
      await _pumpHero(tester, const LiveEventState(live: true, detail: 'Q3 8:42', homeScore: 24, awayScore: 17, situation: _homeRedZone));

      expect(find.byType(PossessionBall), findsOneWidget);
      final ball = _ballCenter(tester);
      // The team name, not the PICK below it, which also reads KC.
      final kc = tester.getCenter(find.text('KC').first);
      expect((ball.dy - kc.dy).abs(), lessThan(4));
      expect(ball.dx, lessThan(kc.dx));
      expect(find.textContaining('1st & Goal at LV 6'), findsOneWidget);
      expect(find.textContaining('Red zone'), findsOneWidget);
    });

    testWidgets('a finished game says only FINAL', (tester) async {
      await _pumpHero(tester, const LiveEventState(live: false, completed: true, detail: 'Final', homeScore: 24, awayScore: 17));

      expect(find.text('FINAL'), findsOneWidget);
      expect(find.text('Final'), findsNothing);
    });

    testWidgets('the line under the venue is a plain divider, not the colored bar', (tester) async {
      await _pumpHero(tester, const LiveEventState(live: false, detail: null, homeScore: null, awayScore: null));

      expect(find.byType(WinProbabilityBar), findsNothing);
      final divider = tester.widget<Divider>(find.byType(Divider));
      expect(divider.color, AppColors.border);
    });
  });

  testWidgets('stacked phone leaders put a divider above every player after the first', (tester) async {
    const comparison = EventLeadersComparison(
      home: TeamLeadersComparison({
        'passing': [
          PlayerStatLineComparison(entityId: '1', name: 'QB', predicted: {'passing_yards': 248}, actual: {'passing_yards': 212}),
        ],
        'rushing': [
          PlayerStatLineComparison(entityId: '2', name: 'RB One', predicted: {'rushing_yards': 61}, actual: {'rushing_yards': 48}),
          PlayerStatLineComparison(entityId: '3', name: 'RB Two', predicted: {'rushing_yards': 18}, actual: {'rushing_yards': 22}),
        ],
        'receiving': [],
        'sacks': [],
      }),
      away: TeamLeadersComparison({'passing': [], 'rushing': [], 'receiving': [], 'sacks': []}),
    );
    tester.view.physicalSize = const Size(380, 1400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(const MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(child: TeamLeadersComparisonPanel(sport: 'nfl', homeAbbr: 'KC', awayAbbr: 'LV', comparison: comparison)),
      ),
    ));

    final dividedRows = tester
        .widgetList<Table>(find.byType(Table))
        .expand((table) => table.children)
        .where((row) => row.decoration is BoxDecoration && (row.decoration as BoxDecoration).border != null)
        .length;
    // One divider: between the two rushers. A lone passer gets none.
    expect(dividedRows, 1);
  });
}
