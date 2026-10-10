import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/theme/app_colors.dart';
import 'package:front_end/core/theme/app_text_styles.dart';
import 'package:front_end/core/widgets/pick_labels.dart';
import 'package:front_end/features/season/season_bracket_round_labels.dart';

Widget _wrap(Widget child) => MaterialApp(home: Scaffold(body: Center(child: child)));

void main() {
  test('lineLabel writes the winner and margin as a betting line', () {
    expect(lineLabel('DET', 6.5), 'DET -6.5');
    // The sign of the margin is the caller's business; the line is always "-N".
    expect(lineLabel('KC', -3.0), 'KC -3.0');
    expect(lineLabel('PHI', 11, trim: true), 'PHI -11');
    expect(lineLabel('TOR', 1.5, trim: true), 'TOR -1.5');
  });

  test('the league final is named per sport', () {
    expect(BracketRoundLabels.finalFor('nfl'), 'Super Bowl');
    expect(BracketRoundLabels.finalFor('nhl'), 'Stanley Cup Final');
    expect(BracketRoundLabels.finalFor('nba'), 'Championship');
  });

  testWidgets('ActualModelKey names white as actual and cyan as the model', (tester) async {
    await tester.pumpWidget(_wrap(const ActualModelKey()));

    expect(find.text(ActualModelKey.actualLabel), findsOneWidget);
    expect(find.text(ActualModelKey.modelLabel), findsOneWidget);
    final swatches = tester.widgetList<Container>(find.byType(Container)).map((c) => (c.decoration as BoxDecoration?)?.color);
    expect(swatches, containsAll([AppColors.ink, AppColors.cyan]));
  });

  testWidgets('VerdictPill reads HIT in green and MISS in red', (tester) async {
    await tester.pumpWidget(_wrap(const VerdictPill(correct: true)));
    expect(tester.widget<Text>(find.text(VerdictPill.hitLabel)).style?.color, AppColors.live);

    await tester.pumpWidget(_wrap(const VerdictPill(correct: false)));
    expect(tester.widget<Text>(find.text(VerdictPill.missLabel)).style?.color, AppColors.neg);
  });

  testWidgets('SlotNumber shrinks a wide number into its slot instead of overflowing', (tester) async {
    await tester.pumpWidget(_wrap(SlotNumber('123456', width: 30, style: AppTextStyles.metricValue())));

    expect(tester.takeException(), isNull);
    expect(tester.getSize(find.byType(SlotNumber)).width, 30);
  });
}
