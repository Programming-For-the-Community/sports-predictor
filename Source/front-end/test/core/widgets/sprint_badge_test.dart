import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/widgets/sprint_badge.dart';

void main() {
  testWidgets('renders the SPRINT label', (tester) async {
    await tester.pumpWidget(const MaterialApp(home: Scaffold(body: SprintBadge())));

    expect(find.text('SPRINT'), findsOneWidget);
  });
}
