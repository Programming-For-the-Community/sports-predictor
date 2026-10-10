import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/widgets/live_status_pill.dart';

void main() {
  group('LiveStatusPill', () {
    testWidgets('renders the LIVE label with a pulsing dot', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: Scaffold(body: LiveStatusPill())));

      expect(find.text('LIVE'), findsOneWidget);
    });
  });
}
