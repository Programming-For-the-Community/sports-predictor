import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/widgets/model_performance_card_view.dart';

import '../../support/mobile_viewport.dart';
import '../../support/model_performance_fixtures.dart';

Widget _wrap(Widget child) => MaterialApp(
      home: Scaffold(body: SingleChildScrollView(child: SizedBox(width: 460, child: child))),
    );

void main() {
  group('win probability (a pick model)', () {
    testWidgets('shows the season and last-week accuracy, how they compare, and the model version', (tester) async {
      await tester.pumpWidget(_wrap(ModelPerformanceCardView(record: pickRecord(), isWeekly: true)));

      expect(find.text('Win Probability'), findsOneWidget);
      expect(find.text('v9'), findsOneWidget);
      expect(find.text('THIS SEASON · ACCURACY'), findsOneWidget);
      expect(find.text('LAST WEEK · ACCURACY'), findsOneWidget);
      expect(find.text('68.4%'), findsOneWidget);
      expect(find.text('75.0%'), findsOneWidget);
      expect(find.text('32 games'), findsOneWidget);
      expect(find.text('12 games'), findsOneWidget);
      expect(find.textContaining('+6.6 pts vs season'), findsOneWidget);
    });

    testWidgets('shows baseline and training figures', (tester) async {
      await tester.pumpWidget(_wrap(ModelPerformanceCardView(record: pickRecord(), isWeekly: true)));

      expect(find.text('+14% BETTER'), findsOneWidget);
      expect(find.text('66.1%'), findsOneWidget);
    });

    testWidgets('is the one card that says CONFIDENCE, with its tiers, counts, and the share right in each', (tester) async {
      await tester.pumpWidget(_wrap(ModelPerformanceCardView(record: pickRecord(), isWeekly: true)));

      expect(find.textContaining('CONFIDENCE'), findsOneWidget);
      expect(find.textContaining('HIGH is 63%+, MED 56–63%, LOW under 56%'), findsOneWidget);
      for (final tier in ['HIGH', 'MED', 'LOW']) {
        expect(find.text(tier), findsOneWidget);
      }
      expect(find.text('9 games'), findsOneWidget);
      expect(find.text('89%'), findsOneWidget);
      expect(find.text('69%'), findsOneWidget);
      expect(find.text('50%'), findsOneWidget);
      expect(find.textContaining('Share of picks that were right.'), findsOneWidget);
    });

    testWidgets('lists recent weeks', (tester) async {
      await tester.pumpWidget(_wrap(ModelPerformanceCardView(record: pickRecord(), isWeekly: true)));

      expect(find.text('RECENT WEEKS'), findsOneWidget);
      expect(find.textContaining('Wk 1'), findsWidgets);
      expect(find.textContaining('62%'), findsOneWidget);
    });
  });

  group('amount models', () {
    for (final width in [360.0, 700.0]) {
      testWidgets('each band puts its tier pill, range and count on one row at ${width}px', (tester) async {
        await tester.runAsync(loadAppFonts);
        await tester.pumpWidget(MaterialApp(
          home: Scaffold(body: SingleChildScrollView(child: SizedBox(width: width, child: ModelPerformanceCardView(record: amountRecord(), isWeekly: true)))),
        ));

        final pill = tester.getRect(find.text('LOW'));
        final range = tester.getRect(find.text('0.5–6.0 pts'));
        final count = [for (var i = 0; i < find.text('11 games').evaluate().length; i++) tester.getRect(find.text('11 games').at(i))].reduce(
              (a, b) => (a.center.dy - pill.center.dy).abs() < (b.center.dy - pill.center.dy).abs() ? a : b,
            );
        expect(range.left, greaterThan(pill.right));
        expect(count.left, greaterThan(range.right));
        for (final other in [range, count]) {
          expect(other.center.dy, closeTo(pill.center.dy, 4));
        }
      });
    }

    testWidgets('score margin shows average miss with units, and predicted-amount bands with ranges - never confidence', (tester) async {
      await tester.pumpWidget(_wrap(ModelPerformanceCardView(record: amountRecord(), isWeekly: true)));

      expect(find.text('Score Margin'), findsOneWidget);
      expect(find.text('THIS SEASON · AVG MISS'), findsOneWidget);
      expect(find.textContaining('10.2'), findsWidgets);
      expect(find.textContaining('8.9'), findsWidgets);
      expect(find.textContaining('1.3 pts closer'), findsOneWidget);
      expect(find.textContaining('PREDICTED AMOUNT'), findsOneWidget);
      expect(find.textContaining('CONFIDENCE'), findsNothing);
      expect(find.text('0.5–6.0 pts'), findsOneWidget);
      expect(find.text('11.5–17.0 pts'), findsOneWidget);
      expect(find.textContaining('within ±10.8 pts of our number'), findsOneWidget);
    });

    testWidgets('each band says whether the model tended to miss high or low, and the season lean is a fact', (tester) async {
      await tester.pumpWidget(_wrap(ModelPerformanceCardView(record: amountRecord(), isWeekly: true)));

      expect(find.text('Missed high by 3.1 pts on average'), findsOneWidget);
      expect(find.text('No clear lean'), findsOneWidget);
      expect(find.text('Missed low by 4.6 pts on average'), findsOneWidget);
      expect(find.text('TENDS TO MISS'), findsOneWidget);
      expect(find.text('High by 2.4 pts'), findsOneWidget);
    });

    testWidgets('a win-probability card has no lean lines', (tester) async {
      await tester.pumpWidget(_wrap(ModelPerformanceCardView(record: pickRecord(), isWeekly: true)));

      expect(find.textContaining('Missed'), findsNothing);
      expect(find.text('TENDS TO MISS'), findsNothing);
    });

    testWidgets('a player prop counts players and shows its range in yards', (tester) async {
      final record = amountRecord(
        modelName: 'player-prop-passing-yards',
        season: 58.2,
        last: 61.4,
        marginOfError: 57.5,
        atTraining: 57.5,
        bands: [
          band('LOW', lo: 120, hi: 193, n: 10, pct: 0.7),
          band('MED', lo: 193, hi: 267, n: 11, pct: 0.55),
          band('HIGH', lo: 267, hi: 340, n: 10, pct: 0.5),
        ],
      );

      await tester.pumpWidget(_wrap(ModelPerformanceCardView(record: record, isWeekly: true)));

      expect(find.text('Player Prop Passing Yards'), findsOneWidget);
      expect(find.text('32 players'), findsOneWidget);
      expect(find.text('10 players'), findsWidgets);
      expect(find.text('120–193 yds'), findsOneWidget);
      expect(find.textContaining('3.2 yds further off'), findsOneWidget);
      expect(find.textContaining('Players grouped into equal thirds'), findsOneWidget);
    });

    testWidgets('a band with too few predictions says too early instead of a percentage', (tester) async {
      final record = amountRecord(bands: [
        band('LOW', lo: 0, hi: 5, n: 10, pct: 0.6),
        band('HIGH', lo: 10, hi: 15, n: 4, early: true),
      ]);

      await tester.pumpWidget(_wrap(ModelPerformanceCardView(record: record, isWeekly: true)));

      expect(find.text('Too early'), findsOneWidget);
      expect(find.text('60%'), findsOneWidget);
      expect(find.text('4 games'), findsOneWidget);
    });
  });

  group('chance models', () {
    testWidgets('a PGA top-10 card counts golfers, says last event, and titles its bands predicted chance', (tester) async {
      await tester.pumpWidget(_wrap(ModelPerformanceCardView(record: chanceRecord(), isWeekly: false)));

      expect(find.text('Top 10 Probability'), findsOneWidget);
      expect(find.text('Biltmore Championship'), findsWidgets);
      expect(find.text('LAST EVENT · ACCURACY'), findsOneWidget);
      expect(find.text('812 golfers'), findsOneWidget);
      expect(find.textContaining('PREDICTED CHANCE'), findsOneWidget);
      expect(find.textContaining('CONFIDENCE'), findsNothing);
      expect(find.textContaining('The top-10 chance we gave each golfer.'), findsOneWidget);
      expect(find.text('0–20%'), findsOneWidget);
      expect(find.text('94%'), findsOneWidget);
      expect(find.text('402 golfers'), findsOneWidget);
      expect(find.text('Too early'), findsOneWidget);
      expect(find.textContaining('Missed'), findsNothing);
    });

    testWidgets('an F1 card counts drivers', (tester) async {
      await tester.pumpWidget(_wrap(ModelPerformanceCardView(
        record: chanceRecord(modelName: 'podium-probability', countNoun: 'drivers'),
        isWeekly: false,
      )));

      expect(find.text('Podium Probability'), findsOneWidget);
      expect(find.text('812 drivers'), findsOneWidget);
      expect(find.textContaining('The podium chance we gave each driver.'), findsOneWidget);
    });

    testWidgets('a PGA round amount reads in strokes', (tester) async {
      final record = amountRecord(modelName: 'round-2', marginOfError: 2.5, atTraining: 2.5, season: 2.6, last: 2.4, bias: -0.3);

      await tester.pumpWidget(_wrap(ModelPerformanceCardView(record: record, isWeekly: false)));

      expect(find.text('Round 2'), findsOneWidget);
      expect(find.textContaining('within ±2.5 strokes of our number'), findsOneWidget);
      expect(find.textContaining('closer'), findsOneWidget);
    });
  });

  testWidgets('a promoted model with nothing graded yet says so, with no numbers', (tester) async {
    await tester.pumpWidget(_wrap(ModelPerformanceCardView(record: emptyRecord(), isWeekly: true)));

    expect(find.text('Player Prop Rushing Touchdowns'), findsOneWidget);
    expect(find.text('NO GRADED PLAYERS YET'), findsOneWidget);
    expect(find.textContaining('THIS SEASON'), findsNothing);
  });

  testWidgets('a sport graded per event says last event and recent events', (tester) async {
    await tester.pumpWidget(_wrap(ModelPerformanceCardView(record: pickRecord(), isWeekly: false)));

    expect(find.text('LAST EVENT · ACCURACY'), findsOneWidget);
    expect(find.text('RECENT EVENTS'), findsOneWidget);
    expect(find.textContaining('LAST WEEK'), findsNothing);
  });

  testWidgets('a model with a season figure but no completed last period shows only the season', (tester) async {
    await tester.pumpWidget(_wrap(ModelPerformanceCardView(record: pickRecord(last: null), isWeekly: true)));

    expect(find.text('THIS SEASON · ACCURACY'), findsOneWidget);
    expect(find.textContaining('LAST WEEK'), findsNothing);
  });
}
