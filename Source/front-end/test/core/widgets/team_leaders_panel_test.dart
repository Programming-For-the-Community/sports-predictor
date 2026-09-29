import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/event_leaders.dart';
import 'package:front_end/core/theme/app_colors.dart';
import 'package:front_end/core/widgets/td_dots.dart';
import 'package:front_end/core/widgets/team_leaders_panel.dart';

import '../../support/mobile_viewport.dart';

/// team_leaders_panel.dart picks its category set (label + which stat keys
/// to show) per sport -- football's passing/rushing/receiving/sacks vs.
/// basketball's scoring/rebounding/assists (see its own _categoriesFor).
/// These lock in that the right label set renders for each, and that a
/// category with no candidates for a given team doesn't render at all.
void main() {
  Widget wrap(Widget child) => MaterialApp(home: Scaffold(body: child));

  testWidgets('nfl leaders render the football category labels', (tester) async {
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

    expect(find.textContaining('0.5 SACKS'), findsOneWidget);
  });

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

    expect(find.textContaining('241 YDS · 2 TD'), findsOneWidget);
    final dots = tester.widget<TdDots>(find.byType(TdDots));
    expect((dots.value, dots.slots), (1.72, 2));
  });

  testWidgets('basketball never shows dots', (tester) async {
    const leaders = EventLeaders(
      home: TeamLeaders({'scoring': [PlayerStatLine(entityId: '1', name: 'Jayson Tatum', stats: {'points': 27.4})], 'rebounding': [], 'assists': []}),
      away: TeamLeaders({'scoring': [], 'rebounding': [], 'assists': []}),
    );

    await tester.pumpWidget(wrap(const TeamLeadersPanel(sport: 'nba', homeAbbr: 'BOS', awayAbbr: 'LAL', leaders: leaders)));

    expect(find.byType(TdDots), findsNothing);
  });

  testWidgets('a football comparison on a wide screen stays one line per player, with dots', (tester) async {
    tester.view.physicalSize = const Size(1000, 1400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(
      wrap(const TeamLeadersComparisonPanel(sport: 'ncaafb', homeAbbr: 'UGA', awayAbbr: 'BAMA', comparison: footballComparison)),
    );

    expect(find.byType(Table), findsNothing);
    expect(find.textContaining('268 YDS 241'), findsOneWidget);
    expect(find.byType(TdDots), findsNWidgets(2));
  });

  for (final width in mobileViewportWidths) {
    testWidgets('a football comparison on a ${width}px phone stacks actual over predicted in aligned columns', (tester) async {
      await pumpAtWidth(
        tester,
        width,
        wrap(const SingleChildScrollView(
          child: TeamLeadersComparisonPanel(sport: 'ncaafb', homeAbbr: 'UGA', awayAbbr: 'BAMA', comparison: footballComparison),
        )),
      );

      expect(tester.takeException(), isNull);
      expect(find.byType(Table), findsNWidgets(3));
      final actualYards = tester.getRect(find.text('268'));
      final predictedYards = tester.getRect(find.text('241'));
      expect(predictedYards.right, closeTo(actualYards.right, 0.5));
      expect(predictedYards.top, greaterThan(actualYards.bottom - 1));
      expect(find.text('1.5'), findsOneWidget);
      expect(find.text('0.5'), findsOneWidget);
      final dots = tester.getRect(find.byType(TdDots).first);
      final predictedTd = tester.getRect(find.text('2').last);
      expect(dots.left, greaterThan(predictedTd.right), reason: 'dots sit right of the number');
    });
  }

  testWidgets('a basketball comparison on a phone does not stack', (tester) async {
    const comparison = EventLeadersComparison(
      home: TeamLeadersComparison({
        'scoring': [PlayerStatLineComparison(entityId: '1', name: 'Jayson Tatum', predicted: {'points': 27}, actual: {'points': 31})],
        'rebounding': [],
        'assists': [],
      }),
      away: TeamLeadersComparison({'scoring': [], 'rebounding': [], 'assists': []}),
    );

    await pumpAtWidth(tester, 360, wrap(const TeamLeadersComparisonPanel(sport: 'nba', homeAbbr: 'BOS', awayAbbr: 'LAL', comparison: comparison)));

    expect(find.byType(Table), findsNothing);
    expect(find.textContaining('31 PTS 27'), findsOneWidget);
  });

  testWidgets('nba leaders render the basketball category labels, one candidate per category', (tester) async {
    const leaders = EventLeaders(
      home: TeamLeaders({
        'scoring': [PlayerStatLine(entityId: '1', name: 'Jayson Tatum', stats: {'points': 27})],
        'rebounding': [PlayerStatLine(entityId: '2', name: 'Al Horford', stats: {'rebounds': 9})],
        'assists': [],
      }),
      away: TeamLeaders({'scoring': [], 'rebounding': [], 'assists': []}),
    );

    await tester.pumpWidget(wrap(const TeamLeadersPanel(sport: 'nba', homeAbbr: 'BOS', awayAbbr: 'LAL', leaders: leaders)));

    expect(find.text('SCORING'), findsOneWidget);
    expect(find.text('REBOUNDING'), findsOneWidget);
    // No candidates for this team in this category -- not rendered at all.
    expect(find.text('ASSISTS'), findsNothing);
    expect(find.textContaining('27 PTS'), findsOneWidget);
    expect(find.text('PASSING'), findsNothing);
  });

  testWidgets('ncaambb leaders use the same basketball category set as nba', (tester) async {
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

  testWidgets('nba leaders comparison renders basketball category labels', (tester) async {
    const comparison = EventLeadersComparison(
      home: TeamLeadersComparison({
        'scoring': [
          PlayerStatLineComparison(entityId: '1', name: 'Jayson Tatum', predicted: {'points': 27}, actual: {'points': 31}),
        ],
        'rebounding': [],
        'assists': [],
      }),
      away: TeamLeadersComparison({'scoring': [], 'rebounding': [], 'assists': []}),
    );

    await tester.pumpWidget(
      wrap(const TeamLeadersComparisonPanel(sport: 'nba', homeAbbr: 'BOS', awayAbbr: 'LAL', comparison: comparison)),
    );

    expect(find.text('SCORING'), findsOneWidget);
    expect(find.textContaining('31 PTS 27'), findsOneWidget);
  });

  testWidgets(
      'nba leaders comparison drops the "(pred N)" wording and colors the actual stat ink and the '
      'predicted one cyan', (tester) async {
    const comparison = EventLeadersComparison(
      home: TeamLeadersComparison({
        'scoring': [
          PlayerStatLineComparison(entityId: '1', name: 'Jayson Tatum', predicted: {'points': 27}, actual: {'points': 31}),
        ],
        'rebounding': [],
        'assists': [],
      }),
      away: TeamLeadersComparison({'scoring': [], 'rebounding': [], 'assists': []}),
    );

    await tester.pumpWidget(
      wrap(const TeamLeadersComparisonPanel(sport: 'nba', homeAbbr: 'BOS', awayAbbr: 'LAL', comparison: comparison)),
    );

    expect(find.textContaining('(pred'), findsNothing);

    final row = tester.widget<Text>(find.byWidgetPredicate(
      (widget) => widget is Text && widget.textSpan?.toPlainText() == '31 PTS 27',
    ));
    final spans = (row.textSpan! as TextSpan).children!.cast<TextSpan>();
    expect(spans.first.style?.color, AppColors.ink); // actual -- live/white
    expect(spans.last.style?.color, AppColors.cyan); // predicted -- blue
  });

  testWidgets('on a wide row, the player name and its stat values share one line', (tester) async {
    const leaders = EventLeaders(
      home: TeamLeaders({
        'passing': [PlayerStatLine(entityId: '1', name: 'QB One', stats: {'passing_yards': 250})],
        'receiving': [], 'rushing': [], 'sacks': [],
      }),
      away: TeamLeaders({'passing': [], 'receiving': [], 'rushing': [], 'sacks': []}),
    );

    await tester.pumpWidget(wrap(const TeamLeadersPanel(sport: 'nfl', homeAbbr: 'KC', awayAbbr: 'LV', leaders: leaders)));

    // Side by side (Row), not stacked (Column) -- the value sits well to
    // the right of the name's own left edge, not directly under it. Not
    // a dy comparison: the Row's default center cross-alignment doesn't
    // guarantee equal top edges for two differently-sized text styles
    // sharing one row.
    final nameX = tester.getTopLeft(find.text('QB One')).dx;
    final valueX = tester.getTopLeft(find.textContaining('250 YDS')).dx;
    expect(valueX, greaterThan(nameX + 50));
  });

  testWidgets(
      'on a narrow row, the player name stacks above its stat values instead of sharing one line '
      '(a real complaint: values were getting ellipsis-clipped on mobile)', (tester) async {
    const comparison = EventLeadersComparison(
      home: TeamLeadersComparison({
        'scoring': [
          PlayerStatLineComparison(entityId: '1', name: 'Jayson Tatum', predicted: {'points': 27}, actual: {'points': 31}),
        ],
        'rebounding': [],
        'assists': [],
      }),
      away: TeamLeadersComparison({'scoring': [], 'rebounding': [], 'assists': []}),
    );

    // Narrow enough that even a stacked (full-width) team column still
    // leaves the row itself under _rowStackBreakpoint (280px), the same
    // squeeze a real phone-width card puts this row under.
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: SizedBox(
          width: 240,
          child: const TeamLeadersComparisonPanel(sport: 'nba', homeAbbr: 'BOS', awayAbbr: 'LAL', comparison: comparison),
        ),
      ),
    ));

    expect(tester.takeException(), isNull);
    final nameY = tester.getTopLeft(find.text('Jayson Tatum')).dy;
    final valueY = tester.getTopLeft(find.textContaining('31 PTS 27')).dy;
    expect(valueY, greaterThan(nameY));
  });

  testWidgets('a compared player with no prediction for a stat shows -- in that cell', (tester) async {
    tester.view.physicalSize = const Size(360, 1400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    const comparison = EventLeadersComparison(
      home: TeamLeadersComparison({
        'passing': [
          PlayerStatLineComparison(
            entityId: '1', name: 'Ty Simpson',
            predicted: {'passing_yards': 241, 'passing_touchdowns': 1.7}, actual: {'passing_yards': 268, 'passing_touchdowns': 2},
          ),
          PlayerStatLineComparison(
            entityId: '4', name: 'Backup QB',
            predicted: {'passing_yards': 40}, actual: {'passing_yards': 12},
          ),
        ],
        'rushing': [],
        'receiving': [],
        'sacks': [],
      }),
      away: TeamLeadersComparison({'passing': [], 'rushing': [], 'receiving': [], 'sacks': []}),
    );

    await tester.pumpWidget(
      wrap(const TeamLeadersComparisonPanel(sport: 'ncaafb', homeAbbr: 'UGA', awayAbbr: 'BAMA', comparison: comparison)),
    );

    expect(find.text('--'), findsWidgets);
    expect(tester.takeException(), isNull);
  });
}
