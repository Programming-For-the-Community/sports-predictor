import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/widgets/confidence_pill.dart';

void main() {
  group('ConfidencePill', () {
    testWidgets('HIGH at or above a 0.13 edge off 50/50', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: Scaffold(body: ConfidencePill(homeWinProbability: 0.63))));

      expect(find.text('HIGH'), findsOneWidget);
    });

    testWidgets('MED at or above a 0.06 edge but below 0.13', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: Scaffold(body: ConfidencePill(homeWinProbability: 0.58))));

      expect(find.text('MED'), findsOneWidget);
    });

    testWidgets('LOW below a 0.06 edge', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: Scaffold(body: ConfidencePill(homeWinProbability: 0.52))));

      expect(find.text('LOW'), findsOneWidget);
    });

    testWidgets('the edge is symmetric -- a strong away favorite is also HIGH', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: Scaffold(body: ConfidencePill(homeWinProbability: 0.37))));

      expect(find.text('HIGH'), findsOneWidget);
    });

    testWidgets('dotOnly renders no text label', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: Scaffold(body: ConfidencePill(homeWinProbability: 0.63, dotOnly: true))));

      expect(find.text('HIGH'), findsNothing);
      expect(find.byType(Text), findsNothing);
    });

    testWidgets('dotOnly still surfaces the tier via a tooltip', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: Scaffold(body: ConfidencePill(homeWinProbability: 0.63, dotOnly: true))));

      expect(find.byTooltip('HIGH'), findsOneWidget);
    });
  });
}
