import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/widgets/final_status_pill.dart';

void main() {
  group('FinalStatusPill', () {
    testWidgets('renders the FINAL label', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: Scaffold(body: FinalStatusPill())));

      expect(find.text('FINAL'), findsOneWidget);
    });

    testWidgets('dotOnly renders no text label', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: Scaffold(body: FinalStatusPill(dotOnly: true))));

      expect(find.text('FINAL'), findsNothing);
    });

    testWidgets('dotOnly still surfaces FINAL via a tooltip', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: Scaffold(body: FinalStatusPill(dotOnly: true))));

      expect(find.byTooltip('FINAL'), findsOneWidget);
    });
  });
}
