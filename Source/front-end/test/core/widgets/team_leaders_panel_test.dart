import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/event_leaders.dart';
import 'package:front_end/core/theme/app_colors.dart';
import 'package:front_end/core/widgets/team_leaders_panel.dart';

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
}
