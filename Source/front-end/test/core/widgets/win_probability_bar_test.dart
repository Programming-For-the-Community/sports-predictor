import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/widgets/win_probability_bar.dart';

List<Expanded> _segments(WidgetTester tester) => tester.widgetList<Expanded>(find.byType(Expanded)).toList();

void main() {
  group('WinProbabilityBar', () {
    testWidgets('splits the bar proportionally to the probability', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: Scaffold(body: WinProbabilityBar(homeWinProbability: 0.7))));

      final segments = _segments(tester);
      expect(segments[0].flex, 700);
      expect(segments[1].flex, 300);
    });

    testWidgets('an exact 50/50 split treats home as favored', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: Scaffold(body: WinProbabilityBar(homeWinProbability: 0.5))));

      final segments = _segments(tester);
      expect(segments[0].flex, 500);
      expect(segments[1].flex, 500);
    });

    testWidgets('clamps the flex away from 0 for a near-certain underdog side', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: Scaffold(body: WinProbabilityBar(homeWinProbability: 0.001))));

      final segments = _segments(tester);
      // flex must stay >= 1 -- a 0 flex would make the segment invisible
      // instead of a thin sliver.
      expect(segments[0].flex, greaterThanOrEqualTo(1));
      expect(segments[1].flex, lessThanOrEqualTo(999));
    });

    testWidgets('clamps the flex away from 1000 for a near-certain home win', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: Scaffold(body: WinProbabilityBar(homeWinProbability: 0.999))));

      final segments = _segments(tester);
      expect(segments[0].flex, lessThanOrEqualTo(999));
      expect(segments[1].flex, greaterThanOrEqualTo(1));
    });

    testWidgets('renders at the requested height', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: Scaffold(body: WinProbabilityBar(homeWinProbability: 0.5, height: 16))));

      final size = tester.getSize(find.byType(WinProbabilityBar));
      expect(size.height, 16);
    });
  });
}
