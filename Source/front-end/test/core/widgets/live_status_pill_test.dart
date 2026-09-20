import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/widgets/live_status_pill.dart';

void main() {
  group('LiveStatusPill', () {
    testWidgets('renders the LIVE label with a pulsing dot', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: Scaffold(body: LiveStatusPill())));

      expect(find.text('LIVE'), findsOneWidget);
    });

    testWidgets('dotOnly renders no text label', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: Scaffold(body: LiveStatusPill(dotOnly: true))));

      expect(find.text('LIVE'), findsNothing);
    });

    testWidgets('dotOnly still surfaces LIVE via a tooltip', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: Scaffold(body: LiveStatusPill(dotOnly: true))));

      expect(find.byTooltip('LIVE'), findsOneWidget);
    });

    test('label is the shared LIVE constant', () {
      expect(LiveStatusPill.label, 'LIVE');
    });
  });
}
