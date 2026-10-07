import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/widgets/confidence_pill.dart';
import 'package:front_end/static/confidence_tiers.dart';

void main() {
  final high = ConfidenceTier.high.minProbability;
  final med = ConfidenceTier.med.minProbability;

  group('ConfidencePill', () {
    testWidgets('HIGH from the high tier win probability', (tester) async {
      await tester.pumpWidget(MaterialApp(home: Scaffold(body: ConfidencePill(homeWinProbability: high))));

      expect(find.text('HIGH'), findsOneWidget);
    });

    testWidgets('MED from the medium tier win probability', (tester) async {
      await tester.pumpWidget(MaterialApp(home: Scaffold(body: ConfidencePill(homeWinProbability: med))));

      expect(find.text('MED'), findsOneWidget);
    });

    testWidgets('LOW below the medium tier win probability', (tester) async {
      await tester.pumpWidget(MaterialApp(home: Scaffold(body: ConfidencePill(homeWinProbability: med - 0.01))));

      expect(find.text('LOW'), findsOneWidget);
    });

    testWidgets('the edge is symmetric -- a strong away favorite is also HIGH', (tester) async {
      await tester.pumpWidget(MaterialApp(home: Scaffold(body: ConfidencePill(homeWinProbability: 1 - high - 0.01))));

      expect(find.text('HIGH'), findsOneWidget);
    });

    testWidgets('dotOnly renders no text label', (tester) async {
      await tester.pumpWidget(MaterialApp(home: Scaffold(body: ConfidencePill(homeWinProbability: high, dotOnly: true))));

      expect(find.text('HIGH'), findsNothing);
      expect(find.byType(Text), findsNothing);
    });

    testWidgets('dotOnly still surfaces the tier via a tooltip', (tester) async {
      await tester.pumpWidget(MaterialApp(home: Scaffold(body: ConfidencePill(homeWinProbability: high, dotOnly: true))));

      expect(find.byTooltip('HIGH'), findsOneWidget);
    });
  });
}
