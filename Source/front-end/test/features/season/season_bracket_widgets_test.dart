import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/season_projection.dart';
import 'package:front_end/features/season/season_bracket_dimensions.dart';
import 'package:front_end/features/season/season_bracket_matchup_card.dart';
import 'package:front_end/features/season/season_bracket_tree.dart';
import 'package:front_end/features/season/season_leaderboards.dart';
import 'package:front_end/features/season/season_march_madness_section.dart';
import 'package:front_end/features/season/season_standings_table.dart';
import 'package:front_end/features/season/season_march_madness_grid.dart';

BracketMatchup _m(String? a, String? b, {String? winner, int? seedA, int? seedB, int? predA, int? predB}) => BracketMatchup(
      teamA: a,
      teamB: b,
      seedA: seedA,
      seedB: seedB,
      status: 'projected',
      predictedWinner: winner ?? a,
      winProbability: 0.6,
      predictedWinsA: predA,
      predictedWinsB: predB,
    );

// `p` prefixes every team id so the 4 regions never share one. Team
// `${p}1` plays in the Round of 64 then skips the Round of 32 (a bye
// there), which draws a dashed skip connector into the Sweet 16.
RegionBracket _region(String p) => RegionBracket(
      rounds: [
        BracketRound(round: 'Round of 64', matchups: [_m('${p}1', '${p}2'), _m('${p}3', '${p}4')]),
        BracketRound(round: 'Round of 32', matchups: [_m('${p}3', '${p}5')]),
        BracketRound(round: 'Sweet 16', matchups: [_m('${p}1', '${p}3')]),
      ],
      champion: '${p}1',
    );

MarchMadnessBracket _bracket({String champion = 'a1'}) => MarchMadnessBracket(
      // Its winner, c4, sits in right-side region C's Round of 64.
      firstFour: [_m('c4', 'c9', winner: 'c4')],
      regions: {'A': _region('a'), 'B': _region('b'), 'C': _region('c'), 'D': _region('d')},
      finalFour: [_m('a1', 'b1'), _m('c1', 'd1')],
      championship: _m('a1', 'c1', winner: champion),
      champion: champion,
      teamNames: const {},
    );

Widget _app(Widget child) => ProviderScope(child: MaterialApp(home: Scaffold(body: SingleChildScrollView(child: child))));

void main() {
  group('MarchMadnessGrid', () {
    testWidgets('renders a right-side First Four card and dashed skip connectors, and repaints on a new bracket',
        (tester) async {
      await tester.pumpWidget(_app(MarchMadnessGrid(sport: 'ncaambb', bracket: _bracket(), regionOrder: const ['A', 'B', 'C', 'D'])));
      await tester.pumpAndSettle();

      expect(find.text('FIRST FOUR'), findsOneWidget);
      expect(tester.takeException(), isNull);

      await tester.pumpWidget(
        _app(MarchMadnessGrid(sport: 'ncaambb', bracket: _bracket(champion: 'c1'), regionOrder: const ['A', 'B', 'C', 'D'])),
      );
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    });

    testWidgets('dragging the bracket content scrolls it horizontally', (tester) async {
      await tester.pumpWidget(_app(MarchMadnessGrid(sport: 'ncaambb', bracket: _bracket(), regionOrder: const ['A', 'B', 'C', 'D'])));
      await tester.pumpAndSettle();
      final horizontal = find.byWidgetPredicate((w) => w is Scrollable && w.axisDirection == AxisDirection.right);
      final before = tester.state<ScrollableState>(horizontal.first).position.pixels;

      await tester.drag(horizontal.first, const Offset(-300, 0));
      await tester.pumpAndSettle();

      final positions = [for (final e in horizontal.evaluate()) (e as StatefulElement).state as ScrollableState];
      expect(positions.first.position.pixels, greaterThan(before));
      expect(positions.last.position.pixels, positions.first.position.pixels);
    });
  });

  group('BracketTree', () {
    // The Play-In shape: '7' wins its first game and skips the next round.
    final rounds = [
      BracketRound(round: 'Play-In', matchups: [_m('7', '8'), _m('9', '10')]),
      BracketRound(round: 'Elimination', matchups: [_m('8', '9')]),
      BracketRound(round: 'First Round', matchups: [_m('2', '7'), _m('1', '8')]),
    ];

    testWidgets('repaints connectors and the skip legend on rebuild', (tester) async {
      await tester.pumpWidget(_app(BracketTree(sport: 'nba', rounds: rounds, teamNames: const {})));
      await tester.pumpAndSettle();
      await tester.pumpWidget(_app(BracketTree(sport: 'nba', rounds: [...rounds], teamNames: const {'7': BracketTeamName(abbreviation: 'LAL')})));
      await tester.pumpAndSettle();

      expect(find.text('LAL'), findsWidgets);
      expect(tester.takeException(), isNull);
    });

    testWidgets('an empty bracket says it is not available yet', (tester) async {
      await tester.pumpWidget(_app(const BracketTree(sport: 'nba', rounds: [], teamNames: {})));

      expect(find.text('Bracket not available yet.'), findsOneWidget);
    });
  });

  group('BracketMatchupCard', () {
    Widget card(BracketMatchup matchup) => _app(SizedBox(
          width: BracketDimensions.cardWidth,
          height: BracketDimensions.cardHeight,
          child: BracketMatchupCard(sport: 'nba', matchup: matchup, teamNames: const {}, cardWidth: BracketDimensions.cardWidth),
        ));

    testWidgets('a series predicted for team B shows B-first predicted record', (tester) async {
      final matchup = BracketMatchup(
        teamA: '1', teamB: '2', status: 'scheduled', predictedWinner: '2', winProbability: 0.6,
        winsA: 0, winsB: 0, predictedWinsA: 2, predictedWinsB: 4,
      );
      await tester.pumpWidget(card(matchup));

      expect(find.textContaining('4-2'), findsOneWidget);
    });

    testWidgets('a bye side shows BYE with its seed', (tester) async {
      await tester.pumpWidget(card(_m('1', null, seedA: 1, seedB: 16)));

      expect(find.text('BYE'), findsOneWidget);
      expect(find.text('16'), findsOneWidget);
    });
  });

  group('MarchMadnessSection', () {
    testWidgets('without all 4 regions falls back to separate trees, listing the First Four first', (tester) async {
      final bracket = MarchMadnessBracket(
        firstFour: [_m('a2', 'x9', winner: 'a2'), _m('y1', 'y2', winner: null)],
        regions: {'A': _region('a'), 'B': _region('b')},
        finalFour: const [],
        teamNames: const {},
      );
      await tester.pumpWidget(_app(MarchMadnessSection(sport: 'ncaambb', bracket: bracket)));
      await tester.pumpAndSettle();

      expect(find.text('Winner → A • Round of 64'), findsOneWidget);
      expect(find.text('Winner advances to Round of 64'), findsOneWidget);
    });
  });

  group('empty states', () {
    testWidgets('leaderboards not computed', (tester) async {
      await tester.pumpWidget(_app(const SeasonLeaderboards(leaderboards: null)));
      expect(find.textContaining("Leaderboards aren't available right now"), findsOneWidget);
    });

    testWidgets('leaderboards with no entries', (tester) async {
      await tester.pumpWidget(_app(const SeasonLeaderboards(leaderboards: {'passing_yards': []})));
      expect(find.text('No leaderboard data yet.'), findsOneWidget);
    });

    testWidgets('no standings', (tester) async {
      await tester.pumpWidget(_app(const StandingsTable(sport: 'nfl', standings: [])));
      expect(find.text('No standings available yet.'), findsOneWidget);
    });
  });
}
