import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/widgets/model_card_grid.dart';
import 'package:front_end/core/widgets/model_performance_card_view.dart';
import 'package:front_end/core/models/model_performance.dart';

import '../../support/mobile_viewport.dart';
import '../../support/model_performance_fixtures.dart';

// pumpAtWidth (test/support/mobile_viewport.dart) renders with the app's real
// fonts at 1.5x system text on a 700px-tall phone and fails the test on any
// text cut off by an ellipsis -- so passing here means no card ever hides part
// of a result, only that it flows onto more lines. 320px is included because
// the smallest phones are where a fixed-width cell breaks first.
const _widths = [320.0, ...mobileViewportWidths];

Widget _page(List<ModelPerformanceRecord> records, {bool isWeekly = true}) => MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: ModelCardGrid<ModelPerformanceRecord>(
            equalHeight: false,
            items: records,
            cardBuilder: (record) => ModelPerformanceCardView(record: record, isWeekly: isWeekly),
          ),
        ),
      ),
    );

void main() {
  for (final width in _widths) {
    testWidgets('every kind of card renders with no overflow or clipped text at ${width}px wide', (tester) async {
      await pumpAtWidth(tester, width, _page(fullNflSet()));

      expect(tester.takeException(), isNull);
    });

    testWidgets('a per-event sport (PGA-style) renders with no overflow or clipped text at ${width}px wide', (tester) async {
      await pumpAtWidth(
        tester,
        width,
        _page([chanceRecord(), chanceRecord(modelName: 'dnf-probability', countNoun: 'drivers'), amountRecord(modelName: 'round-2')], isWeekly: false),
      );

      expect(tester.takeException(), isNull);
    });

    testWidgets('long numbers and a very long model name do not clip at ${width}px wide', (tester) async {
      final record = amountRecord(
        modelName: 'player-prop-receiving-touchdowns-in-the-red-zone',
        season: 1234.56,
        last: 1234.56,
        seasonN: 12345,
        lastN: 4321,
        marginOfError: 999.99,
        atTraining: 999.99,
        bands: [
          band('LOW', lo: 100000, hi: 200000, n: 12345, pct: 1),
          band('MED', lo: 200000, hi: 300000, n: 12345, pct: 0),
          band('HIGH', lo: 300000, hi: 400000, n: 12345, pct: 0.5),
        ],
      );

      await pumpAtWidth(tester, width, _page([record]));

      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('at phone width the cards stack in a single column', (tester) async {
    await pumpAtWidth(tester, 390, _page([pickRecord(), amountRecord()]));

    final first = tester.getTopLeft(find.byType(ModelPerformanceCardView).first);
    final second = tester.getTopLeft(find.byType(ModelPerformanceCardView).last);

    expect(second.dx, first.dx);
    expect(second.dy, greaterThan(first.dy));
  });
}
