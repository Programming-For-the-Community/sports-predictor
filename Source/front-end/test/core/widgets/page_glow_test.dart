import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:front_end/core/widgets/page_glow.dart';

void main() {
  testWidgets('renders a non-interactive glow positioned at the top center', (tester) async {
    await tester.pumpWidget(const MaterialApp(home: Scaffold(body: Stack(children: [PageGlow()]))));

    expect(find.descendant(of: find.byType(PageGlow), matching: find.byType(IgnorePointer)), findsOneWidget);
    final align = tester.widget<Align>(find.descendant(of: find.byType(PageGlow), matching: find.byType(Align)));
    expect(align.alignment, Alignment.topCenter);
  });
}
