import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/models/model_performance.dart';
import 'package:front_end/core/theme/app_colors.dart';
import 'package:front_end/core/widgets/model_performance_history.dart';
import 'package:front_end/static/model_display.dart';

import '../../support/mobile_viewport.dart';
import '../../support/model_performance_fixtures.dart';

Widget _wrap(Widget child) => MaterialApp(home: Scaffold(body: SingleChildScrollView(child: child)));

ModelPerformanceHistoryChart _chart(ModelPerformanceRecord record, {bool isWeekly = true}) =>
    ModelPerformanceHistoryChart(record: record, display: modelDisplay(record.modelName), isWeekly: isWeekly);

/// A record with the given history and per-version figures, keeping the
/// pick fixture's everything else.
ModelPerformanceRecord _withHistory(ModelPerformanceRecord base, List<PerformanceWindow> history, List<PerformanceWindow> versions) =>
    ModelPerformanceRecord(
      modelName: base.modelName,
      version: base.version,
      kind: base.kind,
      bandKind: base.bandKind,
      season: base.season,
      lastPeriod: base.lastPeriod,
      periods: base.periods,
      vsBaselinePct: base.vsBaselinePct,
      atTraining: base.atTraining,
      marginOfError: base.marginOfError,
      bands: base.bands,
      history: history,
      versions: versions,
    );

Finder _versionDividers() =>
    find.byWidgetPredicate((w) => w is CustomPaint && w.painter.runtimeType.toString() == '_DashedLinePainter');

void main() {
  group('versionColor', () {
    test('versions take palette slots in order, wrapping after twelve', () {
      const palette = AppColors.versionPalette;

      expect(versionColor(1, 12), palette[0]);
      expect(versionColor(12, 12), palette[11]);
      expect(versionColor(13, 13), palette[0]);
      expect(versionColor(14, 14), palette[1]);
    });

    test('a version twelve or more behind the newest is gray, as is an unknown one', () {
      expect(versionColor(2, 13), AppColors.versionPalette[1]);
      expect(versionColor(1, 13), AppColors.versionOlder);
      expect(versionColor(null, 5), AppColors.versionOlder);
    });

    test('any twelve consecutive versions get twelve different colours', () {
      expect({for (var v = 7; v <= 18; v++) versionColor(v, 18)}, hasLength(12));
    });
  });

  test('newestVersion is the highest version on the card or in its history', () {
    expect(newestVersion(pickRecord()), 9);
    final ahead = _withHistory(pickRecord(), history([('Wk 1', 0.6, 11)]), const []);
    expect(newestVersion(ahead), 11);
  });

  group('historyAxis', () {
    test('accuracy runs from a coin flip to the next 10% above the best period', () {
      final axis = historyAxis([0.58, 0.72], isAmount: false);

      expect((axis.lo, axis.hi), (0.5, 0.8));
      expect(axis.ticks, [0.5, 0.6, 0.7, 0.8]);
    });

    test('a period below 50% pulls the floor down to it', () {
      expect(historyAxis([0.42, 0.7], isAmount: false).ticks.first, 0.4);
    });

    test('a wide accuracy range ticks every 20%', () {
      expect(historyAxis([0.05, 0.95], isAmount: false).ticks, [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]);
    });

    test('an average miss starts at zero with a round step, at most four up', () {
      expect(historyAxis([4.5, 5.6], isAmount: true).ticks, [0, 2, 4, 6]);
      expect(historyAxis([0.31, 0.77], isAmount: true).ticks, [0, 0.2, 0.4, 0.6, 0.8]);
      expect(historyAxis([118], isAmount: true).ticks, [0, 50, 100, 150]);
    });

    test('nothing to plot still gives a usable axis', () {
      expect(historyAxis(const [], isAmount: true).hi, greaterThan(0));
      expect(historyAxis(const [], isAmount: false).lo, 0.5);
    });

    test('fraction places a value between the bottom and top, clamped', () {
      const axis = HistoryAxis(0.5, 0.8, [0.5, 0.8]);

      expect(axis.fraction(0.65), closeTo(0.5, 1e-9));
      expect(axis.fraction(0.2), 0);
      expect(axis.fraction(0.95), 1);
    });
  });

  test('axisTickText reads as a percent for accuracy and a trimmed number for a miss', () {
    expect(axisTickText(0.6, isAmount: false), '60%');
    expect(axisTickText(4, isAmount: true), '4');
    expect(axisTickText(2.5, isAmount: true), '2.5');
    expect(axisTickText(0.25, isAmount: true), '0.25');
  });

  group('versionLegend', () {
    test('one entry per version, oldest first, in its colour', () {
      final legend = versionLegend(versionFigures([(8, 0.6, 16), (9, 0.71, 28)]), 9);

      expect([for (final e in legend) (e.label, e.value)], [('v8', 0.6), ('v9', 0.71)]);
      expect(legend.first.color, versionColor(8, 9));
    });

    test('versions a full palette behind fold into one "older" entry, weighted by predictions', () {
      final legend = versionLegend(versionFigures([(1, 0.5, 10), (2, 0.8, 30), (14, 0.7, 5)]), 14);

      expect(legend.map((e) => e.label), ['older', 'v14']);
      expect(legend.first.value, closeTo((0.5 * 10 + 0.8 * 30) / 40, 1e-9));
      expect(legend.first.color, AppColors.versionOlder);
    });

    test('an "older" group with nothing graded has no figure', () {
      final legend = versionLegend(const [PerformanceWindow(value: null, n: 0, version: 1)], 13);

      expect((legend.single.label, legend.single.value), ('older', null));
    });
  });

  group('ModelPerformanceHistoryChart', () {
    testWidgets('titles the chart and gives each version its own figure in the legend', (tester) async {
      await tester.pumpWidget(_wrap(_chart(pickRecord())));

      expect(find.text('ACCURACY BY WEEK AND MODEL VERSION'), findsOneWidget);
      expect(find.text('v8 60%'), findsOneWidget);
      expect(find.text('v9 71%'), findsOneWidget);
    });

    testWidgets('labels the y-axis and the first and last period', (tester) async {
      await tester.pumpWidget(_wrap(_chart(pickRecord())));

      for (final tick in ['50%', '60%', '70%', '80%']) {
        expect(find.text(tick), findsOneWidget);
      }
      expect(find.text('Wk 1'), findsOneWidget);
      expect(find.text('Wk 3'), findsOneWidget);
    });

    testWidgets('holding a bar names its period, figure, count and version', (tester) async {
      await tester.pumpWidget(_wrap(_chart(amountRecord(modelName: 'player-prop-passing-yards'))));

      expect(find.byTooltip('Wk 1 · 11.9 yds · 16 players · v5'), findsOneWidget);
    });

    testWidgets('draws a dashed line only where the version changes', (tester) async {
      await tester.pumpWidget(_wrap(_chart(pickRecord())));

      expect(_versionDividers(), findsOneWidget);
    });

    testWidgets('a period with no figure or version still gets its slot', (tester) async {
      final record = _withHistory(pickRecord(), const [PerformanceWindow(value: null, n: 0, label: 'Wk 1')], const []);
      await tester.pumpWidget(_wrap(_chart(record)));

      expect(find.byTooltip('Wk 1 · -- · 0 games'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    test('shows only when there is a finished period to chart', () {
      expect(ModelPerformanceHistoryChart.shows(pickRecord()), isTrue);
      expect(ModelPerformanceHistoryChart.shows(emptyRecord()), isFalse);
    });

    for (final width in [320.0, ...mobileViewportWidths]) {
      testWidgets('a long event name and thirteen versions fit at ${width}px with no overflow or clipped text', (tester) async {
        final periods = [for (var v = 1; v <= 13; v++) ('Biltmore Championship $v', 0.6 + v / 100, v)];
        final record = _withHistory(
          chanceRecord(),
          history(periods, n: 144),
          versionFigures([for (var v = 1; v <= 13; v++) (v, 0.6 + v / 100, 144)]),
        );
        await pumpAtWidth(tester, width, _wrap(Padding(padding: const EdgeInsets.all(24), child: _chart(record, isWeekly: false))));

        expect(find.text('older 61%'), findsOneWidget);
        expect(tester.takeException(), isNull);
      });
    }
  });
}
