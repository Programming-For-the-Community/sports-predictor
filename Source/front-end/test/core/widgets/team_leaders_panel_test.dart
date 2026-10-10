import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/event_leaders.dart';
import 'package:front_end/core/theme/app_colors.dart';
import 'package:front_end/core/widgets/pick_labels.dart';
import 'package:front_end/core/widgets/td_dots.dart';
import 'package:front_end/core/widgets/team_leaders_panel.dart';

import '../../support/mobile_viewport.dart';

Finder _styled(String text, Color color) => find.byWidgetPredicate(
      (widget) => widget is Text && widget.data == text && widget.style?.color == color,
    );

/// team_leaders_panel.dart picks its category set per sport and lays each
/// category out as a table: stat names across the top, one row per player
/// (or, for a comparison on a phone, an actual row over a model row).
void main() {
  Widget wrap(Widget child) => MaterialApp(home: Scaffold(body: SingleChildScrollView(child: child)));

  const footballComparison = EventLeadersComparison(
    home: TeamLeadersComparison({
      'passing': [
        PlayerStatLineComparison(
          entityId: '1', name: 'Ty Simpson',
          predicted: {'passing_yards': 241, 'passing_touchdowns': 1.72}, actual: {'passing_yards': 268, 'passing_touchdowns': 2},
        ),
      ],
      'rushing': [
        PlayerStatLineComparison(
          entityId: '2', name: 'Jam Miller',
          predicted: {'rushing_yards': 64, 'rushing_touchdowns': 0.58}, actual: {'rushing_yards': 52, 'rushing_touchdowns': 1},
        ),
      ],
      'receiving': [],
      'sacks': [
        PlayerStatLineComparison(entityId: '3', name: 'LT Overton', predicted: {'defensive_sacks': 0.62}, actual: {'defensive_sacks': 1.5}),
      ],
    }),
    away: TeamLeadersComparison({'passing': [], 'rushing': [], 'receiving': [], 'sacks': []}),
  );

  const basketballComparison = EventLeadersComparison(
    home: TeamLeadersComparison({
      'scoring': [
        PlayerStatLineComparison(entityId: '1', name: 'Giannis Antetokounmpo', predicted: {'points': 27}, actual: {'points': 31}),
      ],
      'rebounding': [],
      'assists': [],
    }),
    away: TeamLeadersComparison({'scoring': [], 'rebounding': [], 'assists': []}),
  );

  group('projected leaders', () {
    testWidgets('nfl renders the football categories as a table of cyan values', (tester) async {
      const leaders = EventLeaders(
        home: TeamLeaders({
          'passing': [PlayerStatLine(entityId: '1', name: 'QB One', stats: {'passing_yards': 250})],
          'receiving': [],
          'rushing': [],
          'sacks': [],
        }),
        away: TeamLeaders({'passing': [], 'receiving': [], 'rushing': [], 'sacks': []}),
      );

      await tester.pumpWidget(wrap(const TeamLeadersPanel(sport: 'nfl', homeAbbr: 'KC', awayAbbr: 'LV', leaders: leaders)));

      expect(find.text('PASSING'), findsOneWidget);
      expect(find.text('SCORING'), findsNothing);
      expect(find.text('YDS'), findsOneWidget);
      expect(_styled('250', AppColors.cyan), findsOneWidget);
      // The value sits in its own column, right of the name.
      expect(tester.getTopLeft(find.text('250')).dx, greaterThan(tester.getTopLeft(find.text('QB One')).dx + 50));
    });

    testWidgets('a predicted sack total shows to the nearest half sack', (tester) async {
      const leaders = EventLeaders(
        home: TeamLeaders({
          'passing': [],
          'receiving': [],
          'rushing': [],
          'sacks': [PlayerStatLine(entityId: '1', name: 'Edge Rusher', stats: {'defensive_sacks': 0.62})],
        }),
        away: TeamLeaders({'passing': [], 'receiving': [], 'rushing': [], 'sacks': []}),
      );

      await tester.pumpWidget(wrap(const TeamLeadersPanel(sport: 'ncaafb', homeAbbr: 'UGA', awayAbbr: 'BAMA', leaders: leaders)));

      expect(find.text('SACKS'), findsNWidgets(2));
      expect(find.text('0.5'), findsOneWidget);
    });

    testWidgets('a predicted touchdown shows its whole number with dots', (tester) async {
      const leaders = EventLeaders(
        home: TeamLeaders({
          'passing': [PlayerStatLine(entityId: '1', name: 'Ty Simpson', stats: {'passing_yards': 241, 'passing_touchdowns': 1.72})],
          'receiving': [],
          'rushing': [],
          'sacks': [],
        }),
        away: TeamLeaders({'passing': [], 'receiving': [], 'rushing': [], 'sacks': []}),
      );

      await tester.pumpWidget(wrap(const TeamLeadersPanel(sport: 'ncaafb', homeAbbr: 'UGA', awayAbbr: 'BAMA', leaders: leaders)));

      expect(find.text('241'), findsOneWidget);
      expect(find.text('2'), findsOneWidget);
      expect(find.text('TD'), findsOneWidget);
      final dots = tester.widget<TdDots>(find.byType(TdDots));
      expect((dots.value, dots.slots), (1.72, 2));
    });

    testWidgets('nba keeps a separate list per category and never shows dots', (tester) async {
      const leaders = EventLeaders(
        home: TeamLeaders({
          'scoring': [PlayerStatLine(entityId: '1', name: 'Jayson Tatum', stats: {'points': 27.4})],
          'rebounding': [PlayerStatLine(entityId: '2', name: 'Al Horford', stats: {'rebounds': 9})],
          'assists': [],
        }),
        away: TeamLeaders({'scoring': [], 'rebounding': [], 'assists': []}),
      );

      await tester.pumpWidget(wrap(const TeamLeadersPanel(sport: 'nba', homeAbbr: 'BOS', awayAbbr: 'LAL', leaders: leaders)));

      expect(find.text('SCORING'), findsOneWidget);
      expect(find.text('REBOUNDING'), findsOneWidget);
      // No candidates in this category, so no list for it.
      expect(find.text('ASSISTS'), findsNothing);
      expect(find.text('PASSING'), findsNothing);
      expect(find.byType(Table), findsNWidgets(2));
      expect(find.text('PTS'), findsOneWidget);
      expect(find.text('27'), findsOneWidget);
      expect(find.byType(TdDots), findsNothing);
    });

    testWidgets('ncaambb uses the same basketball category set as nba', (tester) async {
      const leaders = EventLeaders(
        home: TeamLeaders({
          'scoring': [PlayerStatLine(entityId: '1', name: 'Player One', stats: {'points': 20})],
          'rebounding': [],
          'assists': [],
        }),
        away: TeamLeaders({'scoring': [], 'rebounding': [], 'assists': []}),
      );

      await tester.pumpWidget(wrap(const TeamLeadersPanel(sport: 'ncaambb', homeAbbr: 'DUKE', awayAbbr: 'UNC', leaders: leaders)));

      expect(find.text('SCORING'), findsOneWidget);
    });

    for (final width in mobileViewportWidths) {
      testWidgets('on a ${width}px phone one team shows at a time, away first', (tester) async {
        const leaders = EventLeaders(
          home: TeamLeaders({
            'passing': [PlayerStatLine(entityId: '1', name: 'Home Quarterback With A Long Name', stats: {'passing_yards': 250, 'passing_touchdowns': 3.2})],
            'receiving': [], 'rushing': [], 'sacks': [],
          }),
          away: TeamLeaders({
            'passing': [PlayerStatLine(entityId: '2', name: 'Away QB', stats: {'passing_yards': 198})],
            'receiving': [], 'rushing': [], 'sacks': [],
          }),
        );

        await pumpAtWidth(tester, width, wrap(const TeamLeadersPanel(sport: 'nfl', homeAbbr: 'KC', awayAbbr: 'LV', leaders: leaders)));

        expect(find.text('Away QB'), findsOneWidget);
        expect(find.textContaining('Home Quarterback'), findsNothing);

        await tester.tap(find.text('KC'));
        await tester.pump();

        expect(tester.takeException(), isNull);
        expect(find.textContaining('Home Quarterback'), findsOneWidget);
        expect(find.text('Away QB'), findsNothing);
      });
    }
  });

  group('predicted against actual', () {
    testWidgets('on a wide screen each stat is actual, model and the gap, side by side', (tester) async {
      tester.view.physicalSize = const Size(1000, 1400);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      await tester.pumpWidget(
        wrap(const TeamLeadersComparisonPanel(sport: 'ncaafb', homeAbbr: 'UGA', awayAbbr: 'BAMA', comparison: footballComparison)),
      );

      expect(find.text('PLAYER PROPS'), findsOneWidget);
      expect(find.byType(ActualModelKey), findsOneWidget);
      expect(find.byType(Table), findsNWidgets(3));
      final actual = tester.getRect(_styled('268', AppColors.ink));
      final model = tester.getRect(_styled('241', AppColors.cyan));
      expect(model.center.dy, closeTo(actual.center.dy, 0.5));
      expect(model.left, greaterThan(actual.right));
      // The gap shows on each category's first stat: yards, and sacks.
      expect(find.text('+27'), findsOneWidget);
      expect(find.text('−12'), findsOneWidget);
      expect(find.text('+1'), findsOneWidget);
      expect(find.byType(TdDots), findsNWidgets(2));
    });

    testWidgets('a live comparison leaves the gap off', (tester) async {
      await tester.pumpWidget(wrap(const TeamLeadersComparisonPanel(
        sport: 'ncaafb', homeAbbr: 'UGA', awayAbbr: 'BAMA', comparison: footballComparison,
        title: 'PLAYER LEADERS -- LIVE', showMiss: false,
      )));

      expect(find.text('PLAYER LEADERS -- LIVE'), findsOneWidget);
      expect(find.text('+27'), findsNothing);
      expect(find.text('±'), findsNothing);
    });

    for (final width in mobileViewportWidths) {
      testWidgets('football on a ${width}px phone stacks the model under the actual in aligned columns', (tester) async {
        await pumpAtWidth(
          tester, width,
          wrap(const TeamLeadersComparisonPanel(sport: 'ncaafb', homeAbbr: 'UGA', awayAbbr: 'BAMA', comparison: footballComparison)),
        );
        await tester.tap(find.text('UGA'));
        await tester.pump();

        expect(tester.takeException(), isNull);
        expect(find.byType(Table), findsNWidgets(3));
        final actualYards = tester.getRect(find.text('268'));
        final predictedYards = tester.getRect(find.text('241'));
        expect(predictedYards.right, closeTo(actualYards.right, 0.5));
        expect(predictedYards.top, greaterThan(actualYards.bottom - 1));
        expect(find.text('1.5'), findsOneWidget);
        expect(find.text('0.5'), findsOneWidget);
        expect(find.text('+27'), findsOneWidget);
        final dots = tester.getRect(find.byType(TdDots).first);
        final predictedTd = tester.getRect(find.text('2').last);
        expect(dots.left, greaterThan(predictedTd.right), reason: 'dots sit right of the number');
      });

      testWidgets('basketball on a ${width}px phone stacks the same way, and a long name wraps', (tester) async {
        await pumpAtWidth(
          tester, width,
          wrap(const TeamLeadersComparisonPanel(sport: 'nba', homeAbbr: 'MIL', awayAbbr: 'MIA', comparison: basketballComparison)),
        );
        await tester.tap(find.text('MIL'));
        await tester.pump();

        expect(tester.takeException(), isNull);
        expect(find.text('Giannis Antetokounmpo'), findsOneWidget);
        final actual = tester.getRect(_styled('31', AppColors.ink));
        final model = tester.getRect(_styled('27', AppColors.cyan));
        expect(model.top, greaterThan(actual.bottom - 1));
        expect(find.text('+4'), findsOneWidget);
        expect(find.textContaining('(pred'), findsNothing);
      });
    }

    testWidgets('a very narrow card still lays out without overflow', (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: SizedBox(
              width: 240,
              child: const TeamLeadersComparisonPanel(sport: 'nba', homeAbbr: 'MIL', awayAbbr: 'MIA', comparison: basketballComparison),
            ),
          ),
        ),
      ));
      await tester.tap(find.text('MIL'));
      await tester.pump();

      expect(tester.takeException(), isNull);
      expect(find.text('31'), findsOneWidget);
    });

    testWidgets('a player with no prediction for a stat shows -- in that cell', (tester) async {
      const comparison = EventLeadersComparison(
        home: TeamLeadersComparison({
          'passing': [
            PlayerStatLineComparison(
              entityId: '1', name: 'Ty Simpson',
              predicted: {'passing_yards': 241, 'passing_touchdowns': 1.7}, actual: {'passing_yards': 268, 'passing_touchdowns': 2},
            ),
            PlayerStatLineComparison(entityId: '4', name: 'Backup QB', predicted: {'passing_yards': 40}, actual: {'passing_yards': 12}),
          ],
          'rushing': [],
          'receiving': [],
          'sacks': [],
        }),
        away: TeamLeadersComparison({'passing': [], 'rushing': [], 'receiving': [], 'sacks': []}),
      );

      await pumpAtWidth(
        tester, 360,
        wrap(const TeamLeadersComparisonPanel(sport: 'ncaafb', homeAbbr: 'UGA', awayAbbr: 'BAMA', comparison: comparison)),
      );
      await tester.tap(find.text('UGA'));
      await tester.pump();

      expect(find.text('--'), findsWidgets);
      expect(tester.takeException(), isNull);
    });
  });

  test('missText signs the gap and rounds like the stat itself', () {
    expect(missText('passing_yards', 268, 241), '+27');
    expect(missText('rushing_yards', 52, 64), '−12');
    expect(missText('points', 27.2, 27), '0');
    expect(missText('defensive_sacks', 1.5, 0.5), '+1');
    expect(missText('defensive_sacks', 0, 0.62), '−0.5');
  });
}
