import 'dart:ui' show SemanticsFlag;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/model_performance.dart';
import 'package:front_end/core/widgets/model_performance_accordion.dart';
import 'package:front_end/core/widgets/model_performance_card_view.dart';
import 'package:front_end/core/widgets/model_performance_history.dart';

import '../../support/mobile_viewport.dart';
import '../../support/model_performance_fixtures.dart';

Widget _list(List<ModelPerformanceRecord> records, {bool isWeekly = true, String seasonLabel = 'THIS SEASON'}) => MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: ModelPerformanceAccordion(records: records, isWeekly: isWeekly, seasonLabel: seasonLabel),
        ),
      ),
    );

final _records = [pickRecord(), amountRecord(), amountRecord(modelName: 'player-prop-passing-yards', season: 58.2, last: 61.4)];

Future<void> _tap(WidgetTester tester, String name) async {
  await tester.ensureVisible(find.text(name));
  await tester.tap(find.text(name));
  await tester.pumpAndSettle();
}

/// The model whose row is open, read from what the open row shows.
String? _openModel(WidgetTester tester) {
  final details = find.byType(ModelPerformanceDetails);
  if (details.evaluate().isEmpty) return null;
  return tester.widget<ModelPerformanceDetails>(details).record.modelName;
}

void main() {
  testWidgets('every row starts closed, showing the name and the two headline figures', (tester) async {
    await tester.pumpWidget(_list(_records));

    expect(find.byType(ModelPerformanceDetails), findsNothing);
    for (final heading in ['MODEL', 'SEASON', 'LAST WEEK']) {
      expect(find.text(heading), findsOneWidget);
    }
    expect(find.text('Win Probability'), findsOneWidget);
    expect(find.text('68.4%'), findsOneWidget);
    expect(find.text('75.0% ▲6.6'), findsOneWidget);
    expect(find.text('10.2 pts'), findsOneWidget);
    expect(find.text('8.9 pts ▲1.3'), findsOneWidget);
    expect(find.text('61.4 yds ▼3.2'), findsOneWidget);
  });

  testWidgets('tapping a row opens the full card under it: badges, both big boxes, the rest and the chart', (tester) async {
    await tester.pumpWidget(_list(_records));
    await _tap(tester, 'Win Probability');

    expect(_openModel(tester), 'win-probability');
    expect(find.text('v9'), findsOneWidget);
    expect(find.text('THIS SEASON · ACCURACY'), findsOneWidget);
    expect(find.text('LAST WEEK · ACCURACY'), findsOneWidget);
    expect(find.byType(ModelPerformanceHistoryChart), findsOneWidget);
    expect(find.text('RECENT WEEKS'), findsOneWidget);
  });

  testWidgets('opening another row closes the one that was open', (tester) async {
    await tester.pumpWidget(_list(_records));
    await _tap(tester, 'Win Probability');
    await _tap(tester, 'Score Margin');

    expect(find.byType(ModelPerformanceDetails), findsOneWidget);
    expect(_openModel(tester), 'score-margin');
  });

  testWidgets('tapping the open row again closes it', (tester) async {
    await tester.pumpWidget(_list(_records));
    await _tap(tester, 'Score Margin');
    await _tap(tester, 'Score Margin');

    expect(_openModel(tester), isNull);
  });

  testWidgets('each row is a button that says whether it is open and reads out its figures', (tester) async {
    final semantics = tester.ensureSemantics();
    await tester.pumpWidget(_list(_records));

    final closed = tester.getSemantics(find.bySemanticsLabel(RegExp(r'^Win Probability\. Season 68\.4%\. Last 75\.0%\.$')));
    expect(closed.hasFlag(SemanticsFlag.isButton), isTrue);
    expect(closed.hasFlag(SemanticsFlag.hasExpandedState), isTrue);
    expect(closed.hasFlag(SemanticsFlag.isExpanded), isFalse);

    await _tap(tester, 'Win Probability');
    final open = tester.getSemantics(find.bySemanticsLabel(RegExp(r'^Win Probability\. Season')));
    expect(open.hasFlag(SemanticsFlag.isExpanded), isTrue);
    expect(tester.getSemantics(find.bySemanticsLabel(RegExp(r'^Score Margin\. Season 10\.2 pts\.'))).hasFlag(SemanticsFlag.isExpanded), isFalse);
    semantics.dispose();
  });

  testWidgets('a per-event sport heads its column LAST EVENT; a rolling window names its window', (tester) async {
    await tester.pumpWidget(_list([chanceRecord()], isWeekly: false, seasonLabel: 'LAST 7 DAYS'));

    expect(find.text('LAST EVENT'), findsOneWidget);
    expect(find.text('LAST 7 DAYS'), findsOneWidget);
  });

  testWidgets('a model with nothing graded yet shows dashes and opens to its empty state', (tester) async {
    await tester.pumpWidget(_list([emptyRecord()]));

    expect(find.text('--'), findsNWidgets(2));
    await _tap(tester, 'Player Prop Rushing Touchdowns');
    expect(find.textContaining('YET'), findsOneWidget);
    expect(find.byType(ModelPerformanceHistoryChart), findsNothing);
  });

  for (final width in [320.0, ...mobileViewportWidths]) {
    testWidgets('closed rows fit at ${width}px with no overflow or clipped text', (tester) async {
      await pumpAtWidth(tester, width, _list(fullNflSet()));

      expect(tester.takeException(), isNull);
    });
  }
}
