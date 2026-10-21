import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/season_projection.dart';
import 'package:front_end/core/models/upset.dart';
import 'package:front_end/core/theme/app_colors.dart';
import 'package:front_end/features/season/season_bracket_dimensions.dart';
import 'package:front_end/features/season/season_bracket_legend.dart';
import 'package:front_end/features/season/season_bracket_matchup_card.dart';
import 'package:front_end/features/season/season_bracket_tree.dart';
import 'package:front_end/features/season/season_leaderboards.dart';
import 'package:front_end/features/season/season_march_madness_section.dart';
import 'package:front_end/features/season/season_standings_table.dart';
import 'package:front_end/features/season/season_march_madness_grid.dart';
import 'package:front_end/features/season/season_round_odds_table.dart';

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
          child: BracketMatchupCard(sport: 'nba', matchup: matchup, teamNames: const {}),
        ));

    testWidgets("a series shows each side's chance and wins as the shown run ends it, and no status line", (tester) async {
      const matchup = BracketMatchup(
        teamA: '1', teamB: '2', status: 'scheduled', predictedWinner: '2', winProbability: 0.6,
        winsA: 1, winsB: 1, predictedWinsA: 3, predictedWinsB: 4,
        chanceA: 0.4, chanceB: 0.6, seriesWinsA: 3, seriesWinsB: 4,
      );
      await tester.pumpWidget(card(matchup));

      expect(find.text('40%'), findsOneWidget);
      expect(find.text('60%'), findsOneWidget);
      expect(find.text('3'), findsOneWidget);
      expect(find.text('4'), findsOneWidget);
      expect(find.textContaining('PROJECTED'), findsNothing);
      expect(find.textContaining('4-3'), findsNothing);
    });

    testWidgets('an older payload without chances falls back to the pick and its probability', (tester) async {
      await tester.pumpWidget(card(_m('1', '2', winner: '2')));

      expect(find.text('40%'), findsOneWidget);
      expect(find.text('60%'), findsOneWidget);
    });

    testWidgets('a finished game shows the chance each side had beforehand, and its score', (tester) async {
      const matchup = BracketMatchup(
        teamA: '1', teamB: '2', status: 'final', predictedWinner: '1', winProbability: 0.7,
        actualWinner: '2', actualHomeScore: 20, actualAwayScore: 24, chanceA: 0, chanceB: 1, flag: UpsetFlag.upset,
      );
      await tester.pumpWidget(card(matchup));

      expect(find.text('70%'), findsOneWidget);
      expect(find.text('30%'), findsOneWidget);
      expect(find.text('24'), findsOneWidget);
      expect(find.text('UPSET'), findsOneWidget);
      final border = (tester.widget<Container>(find.byType(Container).first).decoration! as BoxDecoration).border! as Border;
      expect(border.top.color, AppColors.warn.withValues(alpha: 0.4));
    });

    testWidgets('an upset watch shows its chip and a plain border', (tester) async {
      const matchup = BracketMatchup(
        teamA: '1', teamB: '2', status: 'projected', predictedWinner: '1', winProbability: 0.58,
        chanceA: 0.58, chanceB: 0.42, flag: UpsetFlag.watch,
      );
      await tester.pumpWidget(card(matchup));

      expect(find.text('UPSET WATCH'), findsOneWidget);
      expect(find.text('UPSET'), findsNothing);
      expect(tester.takeException(), isNull);
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

  group('BracketLegend', () {
    testWidgets('a series bracket explains chips, chance and series wins with the run count', (tester) async {
      await tester.pumpWidget(_app(const BracketLegend(showSkip: true, simulation: (series: true, simulations: 2000))));

      expect(find.text(BracketLegend.skipText), findsOneWidget);
      expect(find.text('UPSET'), findsOneWidget);
      expect(find.text('UPSET WATCH'), findsOneWidget);
      expect(find.text(BracketLegend.upsetText(series: true)), findsOneWidget);
      expect(find.text('Series wins in ${BracketLegend.runText(2000)}'), findsOneWidget);
      expect(find.textContaining('2,000'), findsOneWidget);
    });

    testWidgets('a single-game bracket folds the run into the chance entry', (tester) async {
      await tester.pumpWidget(_app(const BracketLegend(simulation: (series: false, simulations: 2000))));

      expect(find.text(BracketLegend.skipText), findsNothing);
      expect(find.text(BracketLegend.upsetText(series: false)), findsOneWidget);
      expect(find.textContaining('Series wins'), findsNothing);
      expect(find.text('Chance to win that game · picks come from ${BracketLegend.runText(2000)}'), findsOneWidget);
    });
  });

  group('RoundOddsTables', () {
    const odds = RoundOdds(
      columns: ['Finals', 'Title'],
      groups: {
        'West': [RoundOddsRow(teamId: 'w1', seed: 1, values: [0.6, 0.35])],
        'East': [
          RoundOddsRow(teamId: 'e1', seed: 1, values: [0.55, 0.3]),
          RoundOddsRow(teamId: 'e8', seed: 8, values: [0, 0]),
          RoundOddsRow(teamId: 'e5', seed: 5, values: [0.001, 0.0004]),
        ],
      },
      simulations: 2000,
    );
    const names = {'e1': BracketTeamName(abbreviation: 'CLE'), 'e8': BracketTeamName(abbreviation: 'MIA'), 'w1': BracketTeamName(abbreviation: 'OKC')};

    testWidgets('one card per group in the given order, with dashes and slivers', (tester) async {
      await tester.pumpWidget(_app(const RoundOddsTables(sport: 'nba', odds: odds, teamNames: names, groupOrder: ['East', 'West'])));

      final east = tester.getTopLeft(find.text('Round odds · East'));
      final west = tester.getTopLeft(find.text('Round odds · West'));
      expect(east.dy < west.dy || east.dx < west.dx, isTrue);
      expect(find.text('ACROSS ALL 2,000 SIMULATIONS'), findsNWidgets(2));
      expect(find.text('55%'), findsOneWidget);
      expect(find.text('—'), findsNWidgets(2));
      expect(find.text('<1%'), findsNWidgets(2));
      expect(find.text('TITLE'), findsNWidgets(2));
    });

    test('formatRoundOdds', () {
      expect(formatRoundOdds(0), '—');
      expect(formatRoundOdds(0.004), '<1%');
      expect(formatRoundOdds(0.996), '>99%');
      expect(formatRoundOdds(1), '100%');
      expect(formatRoundOdds(0.375), '38%');
    });
  });
}
